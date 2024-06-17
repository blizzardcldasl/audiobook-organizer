from flask import Flask, render_template, request, redirect, url_for, send_from_directory, jsonify
import os
import re
import shutil
import csv
import threading
import multiprocessing
import logging
import requests

app = Flask(__name__)
manager = multiprocessing.Manager()
progress_dict = manager.dict()
folder_dict = manager.dict()
pause_event = manager.Event()
pause_event.set()  # Start as not paused

unidentified_books = manager.list()  # List to store unidentified books

logging.basicConfig(filename='app.log', level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

def parse_filename(file_name):
    isbn_pattern = re.compile(r'978[\d]{10}')
    isbn_match = isbn_pattern.search(file_name)
    isbn_number = isbn_match.group(0) if isbn_match else None
    return isbn_number

def get_metadata_from_filename(file_name):
    author_pattern = re.compile(r'^(.*?)\s*-\s*(.*)$')
    series_pattern = re.compile(r'(Vol|Vol\.|Volume|Book)?\s*(\d+(\.\d+)?)\s*-\s*(\d{4})?\s*-\s*(.*)\s*(\{.*\})?', re.IGNORECASE)

    author_match = author_pattern.search(file_name)
    series_match = series_pattern.search(file_name)

    if author_match:
        author = author_match.group(1).strip()
        title = author_match.group(2).strip()
    else:
        author = None
        title = file_name.strip()

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

def fetch_google_books_metadata(title):
    try:
        response = requests.get(f'https://www.googleapis.com/books/v1/volumes?q=intitle:{title}&key=AIzaSyCwFQNwNZvqZEeXtT8i1WcIAQA8OPkx494')
        if response.status_code == 200:
            book_info = response.json()
            if 'items' in book_info:
                book = book_info['items'][0]['volumeInfo']
                author = book.get('authors', ['Unknown Author'])[0]
                title = book.get('title', 'Unknown Title')
                return author, title
    except Exception as e:
        logging.error(f"Error fetching Google Books metadata for {title}: {e}")
    return None, None

def locate_cover_art(file_path, book_title):
    directory = os.path.dirname(file_path)
    cover_file = None
    potential_covers = [f for f in os.listdir(directory) if re.search(r'cover\.jpg$', f, re.IGNORECASE) or re.search(fr'{book_title}.*\.jpg$', f, re.IGNORECASE)]
    
    if potential_covers:
        cover_file = os.path.join(directory, potential_covers[0])

    return cover_file

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
        author, series_sequence, publish_year, title, narrator = get_metadata_from_filename(file)

        if not author or not title:
            # Attempt to fetch metadata from Google Books API
            fetched_author, fetched_title = fetch_google_books_metadata(title)
            if fetched_author and fetched_title:
                author = fetched_author
                title = fetched_title
            else:
                unidentifiable_files.append([file_path, file])
                unidentified_books.append(file_path)  # Add to unidentified books list
                continue

        author_dir = os.path.join(dest_dir, author)
        title_dir = os.path.join(author_dir, title)
        audio_dir = os.path.join(title_dir, 'audiotrack')
        
        if not os.path.exists(audio_dir):
            os.makedirs(audio_dir)

        dest_file_path = os.path.join(audio_dir, file)

        if dest_file_path in processed_files:
            unidentifiable_files.append([file_path, file, 'Duplicate'])
        else:
            shutil.copy(file_path, dest_file_path)
            processed_files[dest_file_path] = file_path

        cover_art = locate_cover_art(file_path, title)
        if cover_art and not os.path.exists(os.path.join(title_dir, 'cover.jpg')):
            shutil.copy(cover_art, os.path.join(title_dir, 'cover.jpg'))

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

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=9977)
