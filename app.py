from flask import Flask, request, jsonify
import yt_dlp
from yt_dlp.utils import DownloadError
import os
import uuid
import boto3
import requests as req
from botocore.client import Config

app = Flask(__name__)

R2_ENDPOINT = os.environ.get('R2_ENDPOINT')
R2_ACCESS_KEY = os.environ.get('R2_ACCESS_KEY')
R2_SECRET_KEY = os.environ.get('R2_SECRET_KEY')
R2_BUCKET = os.environ.get('R2_BUCKET')
R2_PUBLIC_URL = 'https://pub-beee60fdd331469db2333a3036230d02.r2.dev'

def get_s3_client():
    return boto3.client(
        's3',
        endpoint_url=R2_ENDPOINT,
        aws_access_key_id=R2_ACCESS_KEY,
        aws_secret_access_key=R2_SECRET_KEY,
        config=Config(signature_version='s3v4')
    )

@app.route('/health', methods=['GET'])
def health():
    return jsonify({'status': 'ok'})

def try_download(url, output_path):
    download_opts = {
        'format': 'best[ext=mp4][height<=720]/best[height<=720]',
        'outtmpl': output_path,
        'noplaylist': True,
        'quiet': True,
        'no_warnings': True,
        'geo_bypass': True,
        'geo_bypass_country': 'US',
    }
    with yt_dlp.YoutubeDL(download_opts) as ydl:
        ydl.download([url])

@app.route('/search-and-download', methods=['POST'])
def search_and_download():
    data = request.json
    query = data.get('query')
    duration_max = data.get('duration_max', 120)

    if not query:
        return jsonify({'error': 'No query provided'}), 400

    try:
        search_opts = {
            'quiet': True,
            'no_warnings': True,
            'skip_download': True,
            'ignoreerrors': True,
            'geo_bypass': True,
            'geo_bypass_country': 'US',
        }

        with yt_dlp.YoutubeDL(search_opts) as ydl:
            info = ydl.extract_info(f"ytsearch10:{query}", download=False)

        if not info or 'entries' not in info:
            return jsonify({'error': 'No results found'}), 404

        entries = [e for e in info['entries'] if e and e.get('webpage_url')]

        if len(entries) == 0:
            return jsonify({'error': 'No valid entries found'}), 404

        video_info = None
        filename = None
        downloaded_path = None

        for entry in entries:
            try:
                filename = f"{uuid.uuid4()}.mp4"
                output_path = f"/tmp/{filename}"
                try_download(entry['webpage_url'], output_path)
                if os.path.exists(output_path):
                    video_info = entry
                    downloaded_path = output_path
                    break
            except (DownloadError, Exception):
                if 'output_path' in locals() and os.path.exists(output_path):
                    os.remove(output_path)
                continue

        if video_info is None or downloaded_path is None:
            return jsonify({'error': 'No downloadable videos found after trying all results'}), 404

        s3 = get_s3_client()
        s3.upload_file(downloaded_path, R2_BUCKET, filename)
        os.remove(downloaded_path)

        r2_url = f"{R2_PUBLIC_URL}/{filename}"

        return jsonify({
            'success': True,
            'filename': filename,
            'url': r2_url,
            'title': video_info.get('title'),
            'duration': video_info.get('duration'),
        })

    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/download-to-r2', methods=['POST'])
def download_to_r2():
    data = request.json
    url = data.get('url')
    filename = data.get('filename')

    if not url or not filename:
        return jsonify({'error': 'url and filename required'}), 400

    try:
        response = req.get(url, stream=True, timeout=60)
        output_path = f"/tmp/{filename}"

        with open(output_path, 'wb') as f:
            for chunk in response.iter_content(chunk_size=8192):
                f.write(chunk)

        s3 = get_s3_client()
        s3.upload_file(output_path, R2_BUCKET, filename)
        os.remove(output_path)

        r2_url = f"{R2_PUBLIC_URL}/{filename}"
        return jsonify({'success': True, 'url': r2_url, 'filename': filename})

    except Exception as e:
        return jsonify({'error': str(e)}), 500

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
