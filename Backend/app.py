import os
import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from Backend import app

if __name__ == "__main__":
    app.run(debug=True, port=5000)

