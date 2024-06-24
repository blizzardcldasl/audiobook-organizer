import os
import csv
import logging
from flask import Flask, render_template, jsonify, request, send_file
from mutagen.mp3 import MP3
from mutagen.mp4 import MP4
from googleapiclient.discovery import build
from multiprocessing import Pool, Manager

# Configuration
LOG_FILE = "audiobook_organizer.log"
ACCEPT_CSV_FILE = "accepted_files.csv"
DENY_CSV_FILE = "denied_files.csv"
GOOGLE_API_KEY = "AIzaSyCwFQNwNZvqZEeXtT8i1WcIAQA8OPkx494"
GOOGLE_BOOKS_API_SERVICE_NAME = "books"
GOOGLE_BOOKS_API_VERSION = "v1"
INPUT_DIR = "/app/input"
OUTPUT_DIR = "/app/output"

# Setup logging
logging.basicConfig(filename=LOG_FILE, level=logging.DEBUG, format='%(asctime)s - %(levelname)s - %(message)s')

app = Flask(__name__)

# Shared dictionary for progress tracking
manager = Manager()
progress_tracker = manager.dict(total=0, processed=0, current_file="", current_metadata={})

def extract_metadata(file_path):
    try:
        if file_path.endswith('.mp3'):
            audio = MP3(file_path)
        elif file_path.endswith(('.m4a', '.m4b')):
            audio = MP4(file_path)
        else:
            return None
        return {
            'title': audio.get('TIT2', None),
            'author': audio.get('TPE1', None),
            'series': audio.get('TALB', None),
            'book_number': audio.get('TRCK', None),
            'part': audio.get('TPOS', None)
        }
    except Exception as e:
        logging.error(f"Error extracting metadata from {file_path}: {e}")
        return None

def validate_metadata(metadata):
    try:
        service = build(GOOGLE_BOOKS_API_SERVICE_NAME, GOOGLE_BOOKS_API_VERSION, developerKey=GOOGLE_API_KEY)
        query = f"{metadata['title']} {metadata['author']}"
        request = service.volumes().list(source='public', q=query)
        response = request.execute()
        if 'items' in response:
            book = response['items'][0]['volumeInfo']
            metadata['title'] = metadata['title'] or book.get('title')
            metadata['author'] = metadata['author'] or book.get('authors', [None])[0]
        return metadata
    except Exception as e:
        logging.error(f"Error validating metadata: {e}")
        return metadata

def organize_file(file_path):
    metadata = extract_metadata(file_path)
    if not metadata or not metadata['title'] or not metadata['author']:
        logging.warning(f"Unidentifiable file: {file_path}")
        with open(DENY_CSV_FILE, mode='a', newline='') as file:
            writer = csv.writer(file)
            writer.writerow([file_path])
        return
    
    metadata = validate_metadata(metadata)
    if not metadata['title'] or not metadata['author']:
        logging.warning(f"Unidentifiable file after validation: {file_path}")
        with open(DENY_CSV_FILE, mode='a', newline='') as file:
            writer = csv.writer(file)
            writer.writerow([file_path])
        return
    
    progress_tracker['current_file'] = file_path
    progress_tracker['current_metadata'] = metadata

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/progress')
def progress():
    return jsonify(progress=dict(progress_tracker))

@app.route('/action', methods=['POST'])
def action():
    data = request.json
    action = data.get('action')
    file_path = progress_tracker['current_file']
    metadata = progress_tracker['current_metadata']
    
    if action == 'accept':
        with open(ACCEPT_CSV_FILE, mode='a', newline='') as file:
            writer = csv.writer(file)
            writer.writerow([file_path, metadata['title'], metadata['author'], metadata['series'], metadata['book_number'], metadata['part']])
        
        # Determine folder structure based on metadata
        series_title = metadata['series'] or 'Unknown Series'
        book_title = metadata['title']
        author_name = metadata['author']
        book_number = metadata['book_number'] or '1'
        part = metadata['part'] or '1'
        
        if part == '1':  # Single book
            directory = os.path.join(OUTPUT_DIR, series_title, f"{book_title} {series_title} #{book_number}", f"{author_name} - {book_title}")
        else:  # Multi-part book
            directory = os.path.join(OUTPUT_DIR, series_title, f"{book_title} {series_title} #{book_number}", f"{author_name} - {book_title} ({part})")
        
        os.makedirs(directory, exist_ok=True)
        os.rename(file_path, os.path.join(directory, os.path.basename(file_path)))
        logging.info(f"Organized file: {file_path} to {directory}")
    else:
        with open(DENY_CSV_FILE, mode='a', newline='') as file:
            writer = csv.writer(file)
            writer.writerow([file_path])
        logging.info(f"File denied: {file_path}")
    
    progress_tracker['processed'] += 1
    return jsonify(success=True)

def process_files(file_paths):
    for file_path in file_paths:
        organize_file(file_path)

def start_organization(base_path):
    file_paths = [os.path.join(root, file) for root, _, files in os.walk(base_path) for file in files if file.endswith(('.mp3', '.m4a', '.m4b'))]
    progress_tracker['total'] = len(file_paths)

    pool = Pool()
    pool.map(process_files, [file_paths[i::4] for i in range(4)])
    pool.close()
    pool.join()

if __name__ == "__main__":
    progress_tracker['total'] = 0
    progress_tracker['processed'] = 0
    progress_tracker['current_file'] = ""
    progress_tracker['current_metadata'] = {}
    
    start_organization(INPUT_DIR)
    app.run(debug=True, host='0.0.0.0', port=9977)
