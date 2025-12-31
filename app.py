
import os
import uuid
import json
from flask import Flask, render_template, request, redirect, url_for, jsonify, session
from werkzeug.utils import secure_filename
from azure.storage.blob import BlobServiceClient
import logging
import threading
from video_processor import process_video
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", os.urandom(24))

# Configuration
UPLOAD_FOLDER = os.getenv("UPLOAD_FOLDER", 'uploads')
JOBS_FOLDER = os.path.join(UPLOAD_FOLDER, 'jobs') # Persistent storage for jobs
ALLOWED_EXTENSIONS = set(os.getenv("ALLOWED_EXTENSIONS", "mp4,mov,avi").split(","))

if not os.path.exists(UPLOAD_FOLDER):
    os.makedirs(UPLOAD_FOLDER)
if not os.path.exists(JOBS_FOLDER):
    os.makedirs(JOBS_FOLDER)

# Azure Blob Config
USE_AZURE_STORAGE = os.getenv("USE_AZURE_STORAGE", "False").lower() == "true"

if USE_AZURE_STORAGE:
    try:
        # Try Managed Identity / DefaultCredential first
        account_url = os.getenv("AZURE_STORAGE_ACCOUNT_URL")
        connection_string = os.getenv("AZURE_STORAGE_CONNECTION_STRING")

        if account_url:
            from azure.identity import DefaultAzureCredential
            logging.info(f"Using Azure Managed Identity with URL: {account_url}")
            credential = DefaultAzureCredential()
            blob_service_client = BlobServiceClient(account_url, credential=credential)
        elif connection_string:
            logging.info("Using Azure Connection String")
            blob_service_client = BlobServiceClient.from_connection_string(connection_string)
        else:
            logging.error("USE_AZURE_STORAGE is True but no URL or Connection String found.")
            USE_AZURE_STORAGE = False

        if USE_AZURE_STORAGE:
             CONTAINER_NAME = os.getenv("AZURE_CONTAINER_NAME", "skivideos")
             container_client = blob_service_client.get_container_client(CONTAINER_NAME)
             if not container_client.exists():
                 container_client.create_container()
                 logging.info(f"Created container {CONTAINER_NAME}")
                 
    except Exception as e:
        logging.error(f"Failed to initialize Azure Blob Storage: {e}")
        USE_AZURE_STORAGE = False

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

# --- Persistence Helpers ---
def save_job(job_id, data):
    try:
        filepath = os.path.join(JOBS_FOLDER, f"{job_id}.json")
        with open(filepath, 'w') as f:
            json.dump(data, f)
    except Exception as e:
        logging.error(f"Failed to save job {job_id}: {e}")

def load_job(job_id):
    try:
        filepath = os.path.join(JOBS_FOLDER, f"{job_id}.json")
        if os.path.exists(filepath):
            with open(filepath, 'r') as f:
                return json.load(f)
    except Exception as e:
        logging.error(f"Failed to load job {job_id}: {e}")
    return None

def background_processing(job_id, file_source, is_azure=False, model_name=None):
    """
    Background worker to process video.
    """
    logging.info(f"Starting processing for job {job_id} using model: {model_name}")
    temp_video_path = None
    
    try:
        if is_azure:
            # Download from Blob to temp file
            extension = file_source.rsplit('.', 1)[1] if '.' in file_source else 'mp4'
            temp_video_path = os.path.join(UPLOAD_FOLDER, f"temp_{job_id}.{extension}")
            
            logging.info(f"Downloading blob {file_source} to {temp_video_path}")
            blob_client = blob_service_client.get_blob_client(container=CONTAINER_NAME, blob=file_source)
            with open(temp_video_path, "wb") as download_file:
                download_file.write(blob_client.download_blob().readall())
            file_to_process = temp_video_path
        else:
            file_to_process = file_source

        # Create temp dir for frames
        temp_dir = os.path.join("temp", job_id)
        if not os.path.exists(temp_dir):
            os.makedirs(temp_dir)
            
        # Pass model_name to process_video
        analysis_result = process_video(file_to_process, temp_dir, model_name=model_name)
        
        # Update Job Status
        job_data = load_job(job_id) or {}
        job_data.update({
            "status": "completed",
            "data": analysis_result
        })
        save_job(job_id, job_data)
        
        logging.info(f"Job {job_id} completed")
        
        # Cleanup
        if is_azure and temp_video_path and os.path.exists(temp_video_path):
            os.remove(temp_video_path)
            
    except Exception as e:
        logging.error(f"Job {job_id} failed: {e}")
        job_data = load_job(job_id) or {}
        job_data.update({
            "status": "failed",
            "error": str(e)
        })
        save_job(job_id, job_data)

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/upload', methods=['POST'])
def upload_file():
    if 'video' not in request.files:
        return redirect(request.url)
    
    file = request.files['video']
    if file.filename == '':
        return redirect(request.url)
    
    # Get user selected model
    model_name = request.form.get('model_name', 'gemini-3-flash-preview') 
    
    if file and allowed_file(file.filename):
        filename = secure_filename(file.filename)
        job_id = str(uuid.uuid4())
        
        # Initialize Job
        save_job(job_id, {"status": "processing"})
        
        if USE_AZURE_STORAGE:
            # Azure Upload
            try:
                blob_name = f"{job_id}_{filename}"
                blob_client = blob_service_client.get_blob_client(container=CONTAINER_NAME, blob=blob_name)
                blob_client.upload_blob(file)
                
                thread = threading.Thread(target=background_processing, args=(job_id, blob_name, True, model_name))
                thread.start()
            except Exception as e:
                return f"Azure Upload Failed: {e}", 500
        else:
            # Local Save
            filepath = os.path.join(UPLOAD_FOLDER, f"{job_id}_{filename}")
            file.save(filepath)
            
            thread = threading.Thread(target=background_processing, args=(job_id, filepath, False, model_name))
            thread.start()
        
        return redirect(url_for('result', job_id=job_id))
        
    return 'File type not allowed'

@app.route('/result/<job_id>')
def result(job_id):
    job = load_job(job_id)
    if not job:
        return "Job not found", 404
        
    if job.get('status') == 'processing':
        return render_template('result.html', job_id=job_id, status='processing')
    elif job.get('status') == 'completed':
        return render_template('result.html', job_id=job_id, status='completed', analysis=job['data'])
    else:
        return render_template('result.html', job_id=job_id, status='failed', error=job.get('error'))

@app.route('/api/status/<job_id>')
def job_status(job_id):
    job = load_job(job_id)
    if not job:
        return jsonify({"status": "not_found"}), 404
    return jsonify(job)

if __name__ == '__main__':
    app.run(debug=True, port=5000)
