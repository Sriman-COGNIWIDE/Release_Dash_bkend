from flask import Flask, Blueprint, jsonify
from kubernetes import client
import urllib3
import re
import json
from flask_cors import CORS
import os
import ast
from functools import wraps
from collections import defaultdict
import time
from datetime import datetime, timezone
import boto3
from dotenv import load_dotenv
import logging

load_dotenv()

platform_bp = Blueprint('platform', __name__)

app = Flask(__name__)

CLUSTERS = ast.literal_eval(os.getenv("CLUSTERS"))
CACHE_DURATIONS = ast.literal_eval(os.getenv("CACHE_DURATIONS"))
MICROSVC_LABEL_KEY = os.getenv("MICROSERVICE_LABEL_KEY")
def get_short_timezone(zone):
    words = zone.split()
    if len(words)==1:
        short_zone=words
        return words[0]
    short_zone = ''.join(word[0] for word in words)
    return short_zone

def get_formatted_time():
    local_time = datetime.now().astimezone()
    zone = local_time.strftime("%Z")
    short_zone = get_short_timezone(zone)
    return local_time.strftime(f"%I:%M %p {short_zone}")

def get_formatted_date():
    local_time = datetime.now().astimezone()
    return local_time.strftime("%d-%m-%Y")

class EnvironmentCache:
    def __init__(self, maxsize=256):
        self.maxsize = maxsize
        self.cache = defaultdict(dict)
        self.cache_times = defaultdict(dict)
        self.last_access_time = defaultdict(float)

    def get_cache_timestamp(self, env, current_time=None):
        if current_time is None:
            current_time = time.time()
        
        if not self.last_access_time[env]:
            self.last_access_time[env] = current_time
            fetch_time = get_formatted_time()
            fetch_date = get_formatted_date()
            self.set_display_time(env, f"{fetch_date} {fetch_time}")
            return current_time
        
        time_elapsed = current_time - self.last_access_time[env]
        duration = CACHE_DURATIONS.get(env, 300)  
        
        if time_elapsed >= duration:
            self.last_access_time[env] = current_time
            fetch_time = get_formatted_time()
            fetch_date = get_formatted_date()
            self.set_display_time(env, f"{fetch_date} {fetch_time}")
            return current_time
            
        return self.last_access_time[env]

    def get_display_time(self, env):
        return self.cache_times[env].get('display_time')

    def set_display_time(self, env, display_time):
        self.cache_times[env]['display_time'] = display_time

    def cache_clear(self, env=None):
        if env is None:
            self.cache.clear()
            self.last_access_time.clear()
            self.cache_times.clear()
        else:
            if env in self.cache:
                del self.cache[env]
                del self.last_access_time[env]
                if env in self.cache_times:
                    del self.cache_times[env]

    def __call__(self, func):
        @wraps(func)
        def wrapper(cluster_name, env, timestamp):
            current_time = time.time()
            cache_timestamp = self.get_cache_timestamp(env, current_time)
            cache_key = (cluster_name, cache_timestamp)
            
            duration = CACHE_DURATIONS.get(env, 300)
            if current_time - self.last_access_time[env] >= duration:
                self.cache[env].clear()
                self.last_access_time[env] = current_time
                cache_timestamp = current_time
                cache_key = (cluster_name, cache_timestamp)
                self.set_display_time(env, datetime.now().strftime("%d-%m-%Y %I:%M %p "))
            
            if cache_key in self.cache[env]:
                return self.cache[env][cache_key]
            
            result = func(cluster_name, env, cache_timestamp)
            self.cache[env][cache_key] = result
            
            return result
        return wrapper

cluster_cache = EnvironmentCache(maxsize=int(os.getenv("CACHE_MAX_SIZE", "256")))


def get_platform_clusters():
    platform_envs = {k: v for k, v in CLUSTERS.items() if 'platform' in k.lower()}
    return platform_envs

def get_cluster_credentials(cluster_name):
    try:
        secret_name = f"{cluster_name}"
        session = boto3.session.Session()
        client = session.client(
            service_name='secretsmanager',
            region_name=os.getenv("AWS_DEFAULT_REGION")
        )
        
        response = client.get_secret_value(SecretId=secret_name)
        secret = json.loads(response['SecretString'])
        
        return {
            'endpoint': secret.get('cluster_api_endpoint', ''),
            'token': secret.get('bearer_token', '')
        }
    except Exception as e:
        return None

def initialize_k8s_client(cluster_name):
    try:
        cluster_creds = get_cluster_credentials(cluster_name)
        if not cluster_creds:
            return None

        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

        if not cluster_creds['endpoint'] or not cluster_creds['endpoint'].startswith('https://'):
            return None

        configuration = client.Configuration()
        configuration.host = cluster_creds['endpoint']
        configuration.verify_ssl = False
        configuration.api_key = {"authorization": f"Bearer {cluster_creds['token']}"}

        api_client = client.ApiClient(configuration)
        return {
            "apps_v1": client.AppsV1Api(api_client),
            "core_v1": client.CoreV1Api(api_client)
        }
    except Exception as e:
        return None

def get_container_versions(containers):
    if not containers:
        return ""
    
    special_deployments = ['notary', 'customer-node', 'customer2-node', 'forworder-node']
    
    versions = []
    for container in containers:
        image = container.image
        deployment_name = container.name if hasattr(container, 'name') else ''
        
        is_special = any(dep in deployment_name.lower() for dep in special_deployments)
        
        if is_special:
            version_match = re.search(r':(.+)$', image)
            if version_match:
                version = version_match.group(1)
                if version.startswith('v'):
                    version = version[1:]
                versions.append(version)
        else:
            version_match = re.search(r':([^:@]+)(?=[-@]|$)', image)
            if version_match:
                version = version_match.group(1)
                if version.startswith('v'):
                    version = version[1:]
                if '-' in version:
                    version = version.split('-')[0]
                versions.append(version)
    
    return ','.join(versions) if versions else "-"

@cluster_cache
def get_cluster_deployments(cluster_name, env, timestamp):
    k8s_client = initialize_k8s_client(cluster_name)
    if not k8s_client:
        return []

    deployments_info = []
    try:
        namespaces = k8s_client["core_v1"].list_namespace()
        
        for ns in namespaces.items:
            namespace_name = ns.metadata.name
            deployments = k8s_client["apps_v1"].list_namespaced_deployment(namespace_name)
            
            for deployment in deployments.items:
                labels = deployment.metadata.labels
                if labels:
                    label_key, label_value = MICROSVC_LABEL_KEY.split('=')
                    if labels.get(label_key) == label_value:
                        versions = get_container_versions(deployment.spec.template.spec.containers)
                        deployments_info.append({
                            "deployment_name": deployment.metadata.name,
                            "version": versions
                        })
        
        return deployments_info
    except Exception as e:
        return []

def get_environment_type(cluster_name):
    if 'dev' in cluster_name:
        return 'dev'
    elif 'lit' in cluster_name:
        return 'lit'
    elif 'shared' in cluster_name:
        return 'shared'
    elif 'stg' in cluster_name:
        return 'stg'
    elif 'prod' in cluster_name:
        return 'prod'
    return None

def organize_versions_by_microservice(all_deployments):
    microservice_versions = {}
    
    for env_type, deployments in all_deployments.items():
        for deployment in deployments:
            microsvc = deployment["deployment_name"]
            if microsvc not in microservice_versions:
                microservice_versions[microsvc] = {
                    "microsvc": microsvc,
                    "dev": "-",
                    "lit": "-",
                    "shared": "-",
                    "stg": "-",
                    "prod": "-"
                }
            microservice_versions[microsvc][env_type] = deployment["version"]
    
    return list(microservice_versions.values())

@platform_bp.route('/plt/plt-info', methods=['GET'])
def get_platform_info():
    try:
        platform_envs = get_platform_clusters()
        all_deployments = {
            'dev': [], 'lit': [], 'shared': [], 'stg': [], 'prod': []
        }
        
        current_time = time.time()
        display_time = None
        
        for env, clusters in platform_envs.items():
            for cluster_name in clusters:
                env_type = get_environment_type(cluster_name)
                if not env_type:
                    continue
                
                timestamp = cluster_cache.get_cache_timestamp(env, current_time)
                deployments = get_cluster_deployments(cluster_name, env, timestamp)
                all_deployments[env_type].extend(deployments)
                
                if display_time is None:
                    display_time = cluster_cache.get_display_time(env)
        
        organized_data = organize_versions_by_microservice(all_deployments)
        
        if display_time is None:
            fetch_time = get_formatted_time()
            fetch_date = get_formatted_date()
            display_time = f"{fetch_date} {fetch_time}"
        
        return jsonify({
            "status": "success",
            "data": organized_data,
            "date_time": display_time
        })
        
    except Exception as e:
        fetch_time = get_formatted_time()
        fetch_date = get_formatted_date()
        return jsonify({
            "status": "error",
            "error": str(e)
        }), 500

@platform_bp.route('/plt/cache/refresh', methods=['POST'])
def refresh_cache():
    try:
        cluster_cache.cache_clear()
        
        platform_envs = get_platform_clusters()
        all_deployments = {
            'dev': [], 'lit': [], 'shared': [], 'stg': [], 'prod': []
        }
        
        current_time = time.time()
        fetch_time = get_formatted_time()
        fetch_date = get_formatted_date()
        current_display_time = f"{fetch_date} {fetch_time}"
        
        for env, clusters in platform_envs.items():
            for cluster_name in clusters:
                env_type = get_environment_type(cluster_name)
                if not env_type:
                    continue
                
                timestamp = cluster_cache.get_cache_timestamp(env, current_time)
                deployments = get_cluster_deployments(cluster_name, env, timestamp)
                all_deployments[env_type].extend(deployments)
                cluster_cache.set_display_time(env, current_display_time)
        
        organized_data = organize_versions_by_microservice(all_deployments)
        
        return jsonify({
            "status": "success",
            "message": "Cache cleared and data refreshed successfully",
            "data": organized_data,
            "date_time": current_display_time
        })
        
    except Exception as e:
        fetch_time = get_formatted_time()
        fetch_date = get_formatted_date()
        return jsonify({
            "status": "error",
            "error": str(e),
            "date_time": f"{fetch_date} {fetch_time}"
        }), 500

app.register_blueprint(platform_bp)

