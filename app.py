from flask import Flask, request, jsonify
import yt_dlp
import os
import uuid
import boto3
from botocore.client import Config

app = Flask(__name__)

R2_ENDPOINT = os.environ.get('R2_ENDPOINT')
R2_ACCESS_KEY = os.environ.get('R2_ACCESS_KEY')
R2_SECRET_KEY = os.environ.get('R2_SECRET_KEY')
R2_BUCKET = os.environ.get('R2_BUCKET')

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

@app.route('/search-and-download', methods=['POST'])
def search_and_download():
    data = request.json
    query = data.get('query')
    duration_max = data.get('duration_max', 60)

    if not query:
        return jsonify({'error': 'No query provided'}), 400

    try:
        filename = f"{uuid.uuid4()}.mp4"
        output_path = f"/tmp/{filename}"

        ydl_opts = {
            'format': 'bestvideo[ext=mp4][height<=720]+bestaudio[ext=m4a]/best[ext=mp4][height<=720]',
            'outtmpl': output_path,
            'noplaylist': True,
            'match_filter': yt_dlp.utils.match_filter_func(f"duration < {duration_max}"),
            'default_search': 'ytsearch1',
            'quiet': True,
        }

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(f"ytsearch3:{query}", download=False)
            if not info or 'entries' not in info or len(info['entries']) == 0:
                return jsonify({'error': 'No results found for query'}), 404
           video_info = None
            for entry in info['entries']:
                if entry and entry.get('webpage_url'):
                    try:
                        ydl.download([entry['webpage_url']])
                        video_info = entry
                        break
                    except Exception:
                        continue

            if video_info is None:
                return jsonify({'error': 'No downloadable videos found'}), 404

        s3 = get_s3_client()
        s3.upload_file(output_path, R2_BUCKET, filename)

        os.remove(output_path)

        r2_url = f"{R2_ENDPOINT}/{R2_BUCKET}/{filename}"

        return jsonify({
            'success': True,
            'filename': filename,
            'url': r2_url,
            'title': video_info.get('title'),
            'duration': video_info.get('duration'),
        })

    except Exception as e:
        return jsonify({'error': str(e)}), 500

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
