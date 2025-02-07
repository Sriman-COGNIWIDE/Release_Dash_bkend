from flask import Flask,Blueprint, jsonify
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
from botocore.exceptions import ClientError
import boto3
from dotenv import load_dotenv

load_dotenv()

inventory_bp = Blueprint('inventory', __name__)

app = Flask(__name__, static_folder='public')

CACHE_DURATIONS = ast.literal_eval(os.getenv("CACHE_DURATIONS"))
CLUSTERS = ast.literal_eval(os.getenv("CLUSTERS"))

k8s_clients = {env: {} for env in CLUSTERS.keys()}

class EnvironmentCache:
    def __init__(self, maxsize=256):
        self.maxsize = maxsize
        self.cache = defaultdict(dict)
        self.last_access_time = defaultdict(float)

    def get_cache_timestamp(self, env, current_time=None):
        if current_time is None:
            current_time = time.time()
        
        if not self.last_access_time[env]:
            self.last_access_time[env] = current_time
            return current_time
        
        time_elapsed = current_time - self.last_access_time[env]
        duration = CACHE_DURATIONS[env]
        intervals = int(time_elapsed / duration)
        
        return self.last_access_time[env] + (intervals * duration)

    def cache_clear(self, env=None):
        if env is None:
            self.cache.clear()
            self.last_access_time.clear()
        else:
            if env in self.cache:
                del self.cache[env]
                del self.last_access_time[env]

    def __call__(self, func):
        @wraps(func)
        def wrapper(cluster_name, env, timestamp):
            cache_key = (cluster_name, timestamp)
            current_time = time.time()
            cache_timestamp = self.get_cache_timestamp(env, current_time)
            
            if current_time > (cache_timestamp + CACHE_DURATIONS[env]):
                self.cache[env].clear()  
                self.last_access_time[env] = current_time  
                cache_timestamp = current_time
                cache_key = (cluster_name, cache_timestamp)
            
            if cache_key in self.cache[env]:
                return self.cache[env][cache_key]
            
            result = func(cluster_name, env, cache_timestamp)
            self.cache[env][cache_key] = result
            
            if len(self.cache[env]) > self.maxsize:
                oldest_key = min(self.cache[env].keys(), key=lambda k: k[1])
                self.cache[env].pop(oldest_key)
            
            return result
        return wrapper

cluster_cache = EnvironmentCache(maxsize=int(os.getenv("CACHE_MAX_SIZE")))

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

def get_cluster_credentials(cluster_name, env):
    secret_name = f"{cluster_name}"
    session = boto3.session.Session()
    client = session.client(
        service_name='secretsmanager',
        region_name=os.getenv("AWS_DEFAULT_REGION")
    )
    
    response = client.get_secret_value(SecretId=secret_name)
    secret = json.loads(response['SecretString'])
    
    endpoint = secret.get('cluster_api_endpoint', '')
    token = secret.get('bearer_token', '')
    
    if not endpoint or not token:
        raise ValueError(f"Cluster credentials not found for {cluster_name} in {env} environment")
    
    return {
        'endpoint': endpoint,
        'token': token
    }
    
def initialize_k8s_clients(env):
    clusters = CLUSTERS.get(env, [])
    
    for cluster_name in clusters:
        try:
            cluster_creds = get_cluster_credentials(cluster_name, env)
            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

            if cluster_creds and cluster_creds['endpoint']:
                if not cluster_creds['endpoint'].startswith('https://'):
                    continue

                configuration = client.Configuration()
                configuration.host = cluster_creds['endpoint']
                configuration.verify_ssl = False
                configuration.api_key = {"authorization": f"Bearer {cluster_creds['token']}"}

                test_client = client.ApiClient(configuration)
                test_api = client.CoreV1Api(test_client)
                test_api.get_api_resources()

                k8s_clients[env][cluster_name] = {
                    "apps_v1": client.AppsV1Api(test_client),
                    "core_v1": test_api
                }
        except Exception:
            continue

VERSION_PATTERN = re.compile(r':([^:@]+)(?=[-@]|$)')

def extract_version_from_image(image_string):
    match = VERSION_PATTERN.search(image_string)
    if not match:
        return {
            "image_tag": "latest", 
            "version": "latest"
        }
    
    full_tag = match.group(1)

    if full_tag.startswith('v'):
        full_tag = full_tag[1:]
    
    if '-' in full_tag:
        version = full_tag.split('-')[0]
        return {
            "image": image_string,
            "image_tag": full_tag,
            "version": version
        }
    
    return {
        "image": image_string,
        "image_tag": full_tag,
        "version": full_tag
    }

def remove_duplicate_containers(containers_list):

    if not containers_list:
        return []
    
    unique_containers = []
    seen_images = set()
    
    for container in containers_list:
        container_key = f"{container['image']}:{container['version']}"
        
        if container_key not in seen_images:
            seen_images.add(container_key)
            unique_containers.append(container)
    
    return unique_containers

def process_container_images(containers):
    if not containers:
        return []
    
    processed_containers = [extract_version_from_image(container.image) for container in containers]
    
    return remove_duplicate_containers(processed_containers)

def get_cache_timestamp(env):
    return cluster_cache.get_cache_timestamp(env)

@cluster_cache
def get_cluster_info_cached(cluster_name, env, timestamp):
    return get_cluster_info(cluster_name, env, CACHE_DURATIONS[env], timestamp)

def get_cluster_info(cluster_name, env, cache_duration, cache_timestamp):
    if cluster_name not in k8s_clients[env]:
        return {
            "status": "error",
            "error": {
                "type": "ClusterNotFound",
                "message": f"Cluster '{cluster_name}' not found in {env} environment"
            }
        }
    
    clients = k8s_clients[env][cluster_name]
    cluster_info = []
    
    fetch_time = get_formatted_time()
    fetch_date = get_formatted_date()
    
    try:
        namespaces = clients["core_v1"].list_namespace()
        
        for ns in namespaces.items:
            namespace_name = ns.metadata.name
            deployments = clients["apps_v1"].list_namespaced_deployment(namespace_name)
            
            for deployment in deployments.items:
                main_containers = process_container_images(deployment.spec.template.spec.containers)
                
                init_containers = process_container_images(
                    deployment.spec.template.spec.init_containers
                ) if deployment.spec.template.spec.init_containers else []
                
                deployment_info = {
                    "deployment-name": deployment.metadata.name,
                    "namespace": namespace_name,
                    "cluster": cluster_name,
                    "main-containers": main_containers,
                    "init-containers": init_containers,
                }
                cluster_info.append(deployment_info)
        
        return {
            "status": "success", 
            "data": cluster_info, 
            "time": fetch_time, 
            "date": fetch_date
        }
    except Exception as e:
        return {
            "status": "error",
            "error": {
                "type": "ClusterInfoError",
                "message": str(e)
            }
        }

@inventory_bp.route('/inventory/all-envs', methods=['GET'])
def get_all_environments():
    try:
        environments = list(CLUSTERS.keys())
        current_date_time = datetime.now().strftime("%d-%m-%Y %I:%M %p")
        
        return jsonify({
            "status": "success",
            "data": environments,
            "date_time": current_date_time
        })
        
    except Exception as e:
        return jsonify({
            "status": "error",
            "error": {
                "type": "EnvironmentListError",
                "message": str(e)
            }
        }), 500

@inventory_bp.route('/inventory/<env>', methods=['GET'])
def get_deployments_by_env(env):
    response_time = get_formatted_time()
    response_date = get_formatted_date()
    
    try:
        env = env.lower()
        if env not in CLUSTERS:
            return jsonify({
                "status": "error",
                "error": {
                    "type": "InvalidEnvironment",
                    "message": f"Environment '{env}' not supported"
                },
                "date_time": f"{response_date} {response_time}"
            }), 404

        if not k8s_clients.get(env):
            initialize_k8s_clients(env)
        
        if not k8s_clients.get(env):
            return jsonify({
                "status": "warning",
                "message": f"No clusters found for environment: {env}",
                "data": [],
                "date_time": f"{response_date} {response_time}"
            })

        timestamp = get_cache_timestamp(env)
        all_cluster_details = []
        
        for cluster_name in k8s_clients[env].keys():
            result = get_cluster_info_cached(cluster_name, env, timestamp)
            if result.get("status") == "success":
                all_cluster_details.extend(result["data"])
                response_time = result.get("time", response_time)
                response_date = result.get("date", response_date)
        
        return jsonify({
            "status": "success",
            "data": all_cluster_details,
            "date_time": f"{response_date} {response_time}"
        })
            
    except Exception as e:
        return jsonify({
            "status": "error",
            "error": {
                "type": "GeneralException",
                "message": str(e)
            },
            "date_time": f"{response_date} {response_time}"
        }), 500

@inventory_bp.route('/inventory/cache/refresh/<env>', methods=['POST'])
def refresh_env_cache(env):
    response_time = get_formatted_time()
    response_date = get_formatted_date()
    try:
        env = env.lower()
        if env not in CLUSTERS:
            return jsonify({
                "status": "error",
                "error": {
                    "type": "InvalidEnvironment",
                    "message": f"Environment '{env}' not supported"
                }
            }), 404

        cluster_cache.cache_clear(env)
        
        if env in k8s_clients:
            k8s_clients[env].clear()
        
        initialize_k8s_clients(env)

        timestamp = get_cache_timestamp(env)
        all_cluster_details = []
        
        for cluster_name in k8s_clients[env].keys():
            result = get_cluster_info_cached(cluster_name, env, timestamp)
            if result.get("status") == "success":
                all_cluster_details.extend(result["data"])

        if not all_cluster_details:
            return jsonify({
                "status": "warning",
                "message": f"No data found after refreshing cache for {env} environment",
                "data": [],
                "date_time": f"{response_date} {response_time}"
            })

        return jsonify({
            "status": "success",
            "message": f"Cache refreshed for {env} environment",
            "data": all_cluster_details,
            "date_time": f"{response_date} {response_time}"
        })

    except Exception as e:
        return jsonify({
            "status": "error",
            "error": str(e)
        }), 500

@inventory_bp.route('/inventory/cache/clear', methods=['POST'])
def clear_cache():
    try:
        cluster_cache.cache_clear()
        
        for env in k8s_clients:
            k8s_clients[env].clear()
        
        return jsonify({
            "status": "success",
            "message": "Cache cleared successfully",
            "time": get_formatted_time(),
            "date": get_formatted_date()
        })
    except Exception as e:
        return jsonify({
            "status": "error",
            "error": str(e)
        }), 500
