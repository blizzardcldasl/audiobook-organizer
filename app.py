from flask import Flask, render_template, request, redirect, url_for, send_from_directory, jsonify
import os
import re
import shutil
import csv
import threading
import multiprocessing
import logging
import requests
import json
from mutagen.easyid3 import EasyID3
from mutagen.mp4 import MP4
from mutagen.m4a import M4A
from beets.library import Library
from beets.ui import commands

app = Flask(__name__)
manager = multiprocessing.Manager()
progress_dict = manager.dict()
folder_dict = manager.dict()
pause_event = manager.Event()
pause_event.set()  # Start as not paused

unidentified_books = manager.list()  # List to store unidentified books

logging.basicConfig(filename='app.log', level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

GOOGLE_API_KEY = "YOUR_GOOGLE_API_KEY"
BEETS_DB_PATH = '/app/beets_library.db'
LIBRARY = Library(BEETS_DB_PATH)

def parse_filename(file_name):
    isbn_pattern = re.compile(r'978[\d]{10}')
    disc_pattern = re.compile(r'(?:Disc|CD)\s*(\d+)', re.IGNORECASE)
    track_pattern = re.compile(r'(\d+)\s*-\s*(\d+)', re.IGNORECASE)

    isbn_match = isbn_pattern.search(file_name)
    disc_match = disc_pattern.search(file_name)
    track_match = track_pattern.search(file_name)

    isbn_number = isbn_match.group(0) if isbn_match else None
    disc_number = int(disc_match.group(1)) if disc_match else None
    track_number = int(track_match.group(2)) if track_match else int(track_match.group(1)) if track_match else None

    return isbn_number, disc_number, track_number

def get_id3_metadata(file_path):
    try:
        if file_path.endswith('.mp3'):
            audio = EasyID3(file_path)
            disc_number = audio.get('discnumber', [None])[0]
            track_number = audio.get('tracknumber', [None])[0]
            return disc_number, track_number
        elif file_path.endswith(('.m4b', '.m4a')):
            audio = MP4(file_path) if file_path.endswith('.m4b') else M4A(file_path)
            disc_number = audio.get('disk', [None])[0]
            track_number = audio.get('trkn', [None])[0]
            return disc_number, track_number
    except Exception as e:
        logging.error(f"Error reading metadata from {file_path}: {e}")
        return None, None

def get_metadata(file_path):
    file_name = os.path.basename(file_path)
    isbn_number, disc_number_fname, track_number_fname = parse_filename(file_name)
    disc_number_id3, track_number_id3 = get_id3_metadata(file_path)

    disc_number = disc_number_fname or disc_number_id3
    track_number = track_number_fname or track_number_id3

    return isbn_number, disc_number, track_number

def parse_folder_name(folder_name):
    author_pattern = re.compile(r'^(.*?)\s*-\s*(.*)$')
    series_pattern = re.compile(r'(Vol|Vol\.|Volume|Book)?\s*(\d+(\.\d+)?)\s*-\s*(\d{4})?\s*-\s*(.*)\s*(\{.*\})?', re.IGNORECASE)

    author_match = author_pattern.search(folder_name)
    series_match = series_pattern.search(folder_name)

    if author_match:
        author = author_match.group(1).strip()
        title = author_match.group(2).strip()
    else:
        author = None
        title = folder_name.strip()

    if series_match:
        series_sequence = series_match.group(2)
        publish_year = series_match.group(4)
        title = series_match.group(5)
        narrator = series_match.group(6)
    else:
        series_sequence = None
        publish_year = None
        narrator = None

    return author, series_sequence, publish_year, title, narrator

def locate_cover_art(file_path, book_title):
    directory = os.path.dirname(file_path)
    cover_file = None
    potential_covers = [f for f in os.listdir(directory) if re.search(r'cover\.jpg$', f, re.IGNORECASE) or re.search(fr'{book_title}.*\.jpg$', f, re.IGNORECASE)]
    
    if potential_covers:
        cover_file = os.path.join(directory, potential_covers[0])

    return cover_file

def fetch_google_books_metadata(title, author=None):
    query = f'intitle:{title}'
    if author:
        query += f'+inauthor:{author}'
    url = f'https://www.googleapis.com/books/v1/volumes?q={query}&key={GOOGLE_API_KEY}'
    response = requests.get(url)
    if response.status_code == 200:
        data = response.json()
        if 'items' in data:
            return data['items'][0]['volumeInfo']
    return None

def fetch_beets_metadata(file_path):
    try:
        item = LIBRARY.add(file_path)
        return {
            'title': item.title,
            'artist': item.artist,
            'album': item.album,
            'year': item.year,
        }
    except Exception as e:
        logging.error(f"Error fetching metadata from Beets for {file_path}: {e}")
        return None

def update_metadata(file_path, metadata):
    try:
        if file_path.endswith('.mp3'):
            audio = EasyID3(file_path)
            if 'title' in metadata:
                audio['title'] = metadata['title']
            if 'artist' in metadata]:
                audio['artist'] = metadata['artist']
            if 'album' in metadata]:
                audio['album'] = metadata['album']
            audio.save()
        elif file_path.endswith(('.m4b', '.m4a')):
            audio = MP4(file_path) if file_path.endswith('.m4b') else M4A(file_path)
            if 'title' in metadata:
                audio['\xa9nam'] = metadata['title']
            if 'artist' in metadata]:
                audio['\xa9ART'] = metadata['artist']
            if 'album' in metadata]:
                audio['\xa9alb'] = metadata['album']
            audio.save()
    except Exception as e:
        logging.error(f"Error updating metadata for {file_path}: {e}")

def organize_audiobooks_process(source_dirs, dest_dir, process_id, progress_dict, folder_dict, pause_event):
    if not os.path.exists(dest_dir):
        os.makedirs(dest_dir)
    
    unidentifiable_files = []
    processed_files = {}
    file_list = []

    for source_dir in source_dirs:
        for root, dirs, files in os.walk(source_dir):
            for file in files:
                if file.endswith(('.mp3', '.m4b', '.m4a', '.mp4')):
                    file_list.append((root, file))
    
    total_files = len(file_list)
    progress_dict[process_id] = 0
    
    for root, file in file_list:
        while not pause_event.is_set():
            pass  # Wait if paused

        file_path = os.path.join(root, file)
        isbn_number, disc_number, track_number = get_metadata(file_path)
        author, series_sequence, publish_year, title, narrator = parse_folder_name(file)

        if isbn_number:
            title = f'ISBN-{isbn_number}'

        if not title:
            unidentifiable_files.append([file_path, file])
            unidentified_books.append(file_path)  # Add to unidentified books list
            continue

        author_dir = os.path.join(dest_dir, author if author else "Unknown Author")
        title_dir = os.path.join(author_dir, title)
        audio_dir = os.path.join(title_dir, 'audiotrack')
        
        if not os.path.exists(audio_dir):
            os.makedirs(audio_dir)

        dest_file_path = os.path.join(audio_dir, f'{disc_number or ""}_{track_number or ""}_{file}')

        if dest_file_path in processed_files:
            unidentifiable_files.append([file_path, file, 'Duplicate'])
        else:
            shutil.copy(file_path, dest_file_path)
            processed_files[dest_file_path] = file_path

        cover_art = locate_cover_art(file_path, title)
        if cover_art and not os.path.exists(os.path.join(title_dir, 'cover.jpg')):
            shutil.copy(cover_art, os.path.join(title_dir, 'cover.jpg'))
        
        google_metadata = fetch_google_books_metadata(title, author)
        beets_metadata = fetch_beets_metadata(file_path)
        
        metadata_sources = {
            'google': google_metadata,
            'beets': beets_metadata,
        }
        
        folder_dict[process_id] = root
        progress_dict[process_id] += 1
    
    with open('/mnt/user/Books/unidentifiable_files.csv', mode='w', newline='') as file:
        writer = csv.writer(file)
        writer.writerow(['Origin Location', 'File Name', 'Note'])
        for unidentifiable in unidentifiable_files:
            if len(unidentifiable) == 2:
                writer.writerow(unidentifiable + ['Unidentifiable'])
            else:
                writer.writerow(unidentifiable)
    
    logging.info(f"Process {process_id}: Audiobook organization completed.")

def organize_audiobooks(source_dirs, dest_dir, num_processes=4):
    processes = []
    for i in range(num_processes):
        p = multiprocessing.Process(target=organize_audiobooks_process, args=(source_dirs, dest_dir, i, progress_dict, folder_dict, pause_event))
        processes.append(p)
        p.start()
    for p in processes:
        p.join()

@app.route('/')
def index():
    return render_template('index.html', progress_dict=progress_dict, folder_dict=folder_dict, unidentified_books=unidentified_books)

@app.route('/start_organizing', methods=['POST'])
def start_organizing():
    source_directories = ['/mnt/user/Books/Audiobooks', '/mnt/user/Downloads/completed/audiobooks']
    destination_directory = '/mnt/user/Books/Audiobooks_new'
    threading.Thread(target=organize_audiobooks, args=(source_directories, destination_directory)).start()
    return redirect(url_for('index'))

@app.route('/pause', methods=['POST'])
def pause():
    pause_event.clear()  # Pause processing
    return redirect(url_for('index'))

@app.route('/resume', methods=['POST'])
def resume():
    pause_event.set()  # Resume processing
    return redirect(url_for('index'))

@app.route('/progress')
def get_progress():
    return jsonify({'progress_dict': progress_dict.copy(), 'folder_dict': folder_dict.copy()})

@app.route('/download_csv')
def download_csv():
    return send_from_directory('/mnt/user/Books', 'unidentifiable_files.csv', as_attachment=True)

@app.route('/update_book_info', methods=['POST'])
def update_book_info():
    file_path = request.form['file_path']
    title = request.form.get('title')
    author = request.form.get('author')
    isbn = request.form.get('isbn')
    amazon_id = request.form.get('amazon_id')
    
    metadata = {}
    
    if title:
        metadata['title'] = title
    if author:
        metadata['artist'] = author
    if isbn:
        metadata['isbn'] = isbn
    if amazon_id:
        metadata['amazon_id'] = amazon_id
    
    update_metadata(file_path, metadata)
    
    if file_path in unidentified_books:
        unidentified_books.remove(file_path)
    
    return redirect(url_for('index'))

@app.route('/search_metadata', methods=['POST'])
def search_metadata():
    file_path = request.form['file_path']
    sources = request.form.getlist('sources')
    
    metadata = {}
    
    if 'google' in sources:
        metadata['google'] = fetch_google_books_metadata(title, author)
    
    if 'beets' in sources:
        metadata['beets'] = fetch_beets_metadata(file_path)
    
    return render_template('metadata_selection.html', metadata=metadata, file_path=file_path)

@app.route('/apply_metadata', methods=['POST'])
def apply_metadata():
    file_path = request.form['file_path']
    source = request.form['source']
    
    metadata = json.loads(request.form['metadata'])
    
    update_metadata(file_path, metadata[source])
    
    if file_path in unidentified_books:
        unidentified_books.remove(file_path)
    
    return redirect(url_for('index'))

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=9977)
