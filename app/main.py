import os
import base64
from flask import Flask, request, jsonify, Response
from pymongo import MongoClient
import httpx
from urllib.parse import urlparse, unquote
from cachetools import TTLCache
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

# Set up rate limiting
limiter = Limiter(
    get_remote_address,
    app=app,
    default_limits=["100 per minute"]
)



# Cache setup to store video info for 5 minutes
cache = TTLCache(maxsize=100, ttl=300)

MAX_STREAM_SIZE = 50 * 1024 * 1024  # 50 MB
mongo_uri = os.getenv('MONGO_URI', 'mongodb+srv://durgesh:empty_VOID1379@cluster0.ibkqlvr.mongodb.net/')
mongo_client = MongoClient(mongo_uri, connect=False)
db = mongo_client['video_db']
videos_collection = db['videos']

headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9",
}

async def get_video_info(url):
    if url in cache:
        return cache[url]
    
    api_url = f'https://tera.instavideosave.com/?url={url}'
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.get(api_url, headers=headers)
        response.raise_for_status()
        video_data = response.json()
        cache[url] = video_data
        return video_data

@app.route('/api/v1/save_video_info', methods=['GET'])
@limiter.limit("10 per minute")
async def save_video_info():
    encoded_url = request.args.get('url')
    
    if not encoded_url:
        return jsonify({"error": "Missing URL parameter"}), 400

    try:
        # Decode the base64 URL
        decoded_url = base64.b64decode(encoded_url).decode('utf-8')
        # Unquote the URL to handle any URL-encoded characters
        user_url = unquote(decoded_url)

        if not urlparse(user_url).scheme:
            return jsonify({"error": "Invalid URL"}), 400

        video_data = await get_video_info(user_url)
        video_name = video_data['video'][0]['name']
        video_download_url = video_data['video'][0]['video']
        video_thumbnail = video_data['video'][0]['thumbnail']

        existing_video = videos_collection.find_one({"filename": video_name})
        if existing_video:
            return jsonify({"message": "ok", "video_download": str(video_download_url), 'video_name': video_name, 'video_thumbnail': video_thumbnail }), 200

        metadata = {
            "filename": video_name,
            "user_input_url": user_url,
            "video_info": video_data  
        }

        video_id = videos_collection.insert_one(metadata).inserted_id

        return jsonify({"message": "ok", "video_download": str(video_download_url), 'video_name': video_name}), 200

    except base64.binascii.Error:
        return jsonify({"error": "Invalid base64 encoded URL"}), 400
    except httpx.HTTPStatusError:
        return jsonify({"error": "Invalid URL"}), 500
    except Exception as e:
        return jsonify({"error": "An internal server error occurred", "message": str(e)}), 500

@app.route('/api/v1/admin/db_info', methods=['GET'])
@limiter.limit("5 per minute")
def get_db_info():
    try:
        total_files = videos_collection.count_documents({})
        total_size = videos_collection.aggregate([
            {"$group": {"_id": None, "total": {"$sum": {"$strLenCP": "$video_info.video"}}}}
        ]).next()['total']

        video_list = list(videos_collection.find({}, {
            "_id": 1, 
            "filename": 1, 
            "metadata": 1
        }))

        for video in video_list:
            video['_id'] = str(video['_id'])

        db_info = {
            "total_files": total_files,
            "total_size_bytes": total_size,
            "total_size_mb": round(total_size / (1024 * 1024), 2),
            "video_list": video_list
        }

        return jsonify(db_info), 200

    except Exception as e:
        return jsonify({"error": "An internal server error occurred"}), 500

@app.route('/api/v1/stream_video', methods=['GET'])
async def stream_video():
    encoded_url = request.args.get('url')
    if not encoded_url:
        return jsonify({"error": "Missing URL parameter"}), 400

    try:
        decoded_url = base64.b64decode(encoded_url).decode('utf-8')
        user_url = unquote(decoded_url)
        if not urlparse(user_url).scheme:
            return jsonify({"error": "Invalid URL"}), 400

        async with httpx.AsyncClient() as client:
            async with client.stream("GET", user_url, timeout=30) as response:
                response.raise_for_status()

                async def generate():
                    async for chunk in response.aiter_bytes():
                        yield chunk

                return Response(generate(), content_type=response.headers['Content-Type'])

    except base64.binascii.Error:
        return jsonify({"error": "Invalid base64 encoded URL"}), 400
    except httpx.ReadTimeout:
        return jsonify({"error": "Request timed out"}), 504
    except httpx.HTTPStatusError:
        return jsonify({"error": "Invalid URL or video not available"}), 500
    except Exception as e:
        return jsonify({"error": "An internal server error occurred", "message": str(e)}), 500

@app.route('/api/v1/health', methods=['GET'])
def health_check():
    try:
        mongo_client.admin.command('ping')
        return jsonify({"status": "healthy"}), 200
    except Exception as e:
        return jsonify({"status": "unhealthy"}), 500


if __name__ == '__main__':
    app.run(debug=False, threaded=True)
