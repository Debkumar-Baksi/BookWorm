# app.py
from flask import Flask, render_template, request, session, redirect, url_for
import requests
import os
import json
from datetime import datetime
import hashlib
from functools import wraps

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'default-secret-key')

# Get API credentials from environment variables or set them here
API_KEY = os.environ.get('GOOGLE_API_KEY', 'AIzaSyDQZUzJ1s-Fq_lfDDIhEFgh8hXsnPLxRSM')
CX = os.environ.get('GOOGLE_CX', '027240d691ea5422b')

# Additional book sources
OPEN_LIBRARY_API = "https://openlibrary.org/search.json"
PROJECT_GUTENBERG_API = "https://gutendex.com/books"

# Simple cache implementation
CACHE_DIR = "cache"
if not os.path.exists(CACHE_DIR):
    os.makedirs(CACHE_DIR)

def cache_results(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        # Create a cache key based on the function arguments
        key = hashlib.md5(str(args).encode() + str(kwargs).encode()).hexdigest()
        cache_file = os.path.join(CACHE_DIR, f"{key}.json")
        
        # Check if we have a valid cache file (less than 1 day old)
        if os.path.exists(cache_file):
            file_age = datetime.now().timestamp() - os.path.getmtime(cache_file)
            if file_age < 86400:  # 24 hours in seconds
                with open(cache_file, 'r') as f:
                    return json.load(f)
        
        # If no cache or expired, call the function
        result = func(*args, **kwargs)
        
        # Save results to cache
        with open(cache_file, 'w') as f:
            json.dump(result, f)
            
        return result
    return wrapper

@cache_results
def search_google_pdf(query, start=1):
    """Search for PDF books using Google Custom Search API"""
    url = "https://www.googleapis.com/customsearch/v1"
    params = {
        "q": f"{query} filetype:pdf",
        "key": API_KEY,
        "cx": CX,
        "start": start
    }
    
    try:
        response = requests.get(url, params=params)
        data = response.json()
        
        results = []
        if 'items' in data:
            for item in data.get("items", []):
                results.append({
                    "title": item["title"],
                    "link": item["link"],
                    "snippet": item.get("snippet", "No description available"),
                    "source": "Google"
                })
        
        total_results = int(data.get("searchInformation", {}).get("totalResults", 0))
        return {"results": results, "total": total_results}
    except Exception as e:
        print(f"Error in Google search: {e}")
        return {"results": [], "total": 0}

@cache_results
def search_open_library(query, page=1):
    """Search for books in Open Library"""
    params = {
        "q": query,
        "page": page,
        "limit": 20,
        "fields": "key,title,author_name,first_publish_year,edition_count"
    }
    
    try:
        response = requests.get(OPEN_LIBRARY_API, params=params)
        data = response.json()
        
        results = []
        for doc in data.get("docs", []):
            author = ", ".join(doc.get("author_name", ["Unknown Author"]))
            results.append({
                "title": doc.get("title", "Untitled"),
                "link": f"https://openlibrary.org{doc.get('key', '')}/epub",
                "snippet": f"By {author} ({doc.get('first_publish_year', 'Year unknown')}). {doc.get('edition_count', 0)} editions available.",
                "source": "Open Library"
            })
        
        return {"results": results, "total": data.get("numFound", 0)}
    except Exception as e:
        print(f"Error in Open Library search: {e}")
        return {"results": [], "total": 0}

@cache_results
def search_gutenberg(query, page=1):
    """Search for books in Project Gutenberg"""
    params = {
        "search": query,
        "page": page,
        "languages": "en"
    }
    
    try:
        response = requests.get(PROJECT_GUTENBERG_API, params=params)
        data = response.json()
        
        results = []
        for book in data.get("results", []):
            authors = ", ".join([author.get("name", "") for author in book.get("authors", [])])
            formats = book.get("formats", {})
            
            # Look for PDF or EPUB format
            download_link = formats.get("application/pdf", formats.get("application/epub+zip", ""))
            if not download_link:
                # Fallback to text format
                download_link = formats.get("text/html", formats.get("text/plain", ""))
            
            if download_link:
                results.append({
                    "title": book.get("title", "Untitled"),
                    "link": download_link,
                    "snippet": f"By {authors}. {', '.join(book.get('subjects', [])[:3])}",
                    "source": "Project Gutenberg"
                })
        
        return {"results": results, "total": data.get("count", 0)}
    except Exception as e:
        print(f"Error in Gutenberg search: {e}")
        return {"results": [], "total": 0}

def aggregate_search(query, page=1):
    """Combine results from multiple sources"""
    google_page = (page - 1) % 10 + 1  # Google only supports pages 1-10
    google_start = (google_page - 1) * 10 + 1
    
    # Search all sources
    google_results = search_google_pdf(query, google_start)
    open_library_results = search_open_library(query, page)
    gutenberg_results = search_gutenberg(query, page)
    
    # Combine results
    all_results = []
    all_results.extend(google_results["results"])
    all_results.extend(open_library_results["results"])
    all_results.extend(gutenberg_results["results"])
    
    # Calculate total
    total = google_results["total"] + open_library_results["total"] + gutenberg_results["total"]
    
    return {
        "results": all_results,
        "total": total,
        "source_stats": {
            "google": len(google_results["results"]),
            "open_library": len(open_library_results["results"]),
            "gutenberg": len(gutenberg_results["results"])
        }
    }

@app.route('/', methods=['GET', 'POST'])
def index():
    if request.method == 'POST':
        query = request.form.get('query', '')
        session['query'] = query
        return redirect(url_for('search', page=1))
    
    return render_template('index.html')

@app.route('/search', methods=['GET'])
def search():
    query = session.get('query', request.args.get('query', ''))
    if not query:
        return redirect(url_for('index'))
    
    page = int(request.args.get('page', 1))
    
    search_results = aggregate_search(query, page)
    
    # Calculate pagination values
    results_per_page = len(search_results["results"])
    total_results = min(search_results["total"], 500)  # Cap at 500 to avoid excessive paging
    if results_per_page > 0:
        total_pages = (total_results + results_per_page - 1) // results_per_page
    else:
        total_pages = 0
    # total_pages = (total_results + results_per_page - 1) // results_per_page
    
    return render_template(
        'search.html',
        query=query,
        results=search_results["results"],
        total_results=total_results,
        current_page=page,
        total_pages=total_pages,
        source_stats=search_results["source_stats"]
    )

if __name__ == '__main__':
    app.run(debug=True)