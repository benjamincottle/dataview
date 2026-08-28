import requests
from flask import Flask, render_template
from flask_caching import Cache
from werkzeug.middleware.proxy_fix import ProxyFix
import logging
import os
from urllib.parse import unquote
from bs4 import BeautifulSoup
import camelot
import pandas as pd
from pathlib import Path
import io
import threading


pd.options.mode.copy_on_write = True
app = Flask(__name__)
app.config["TEMPLATES_AUTO_RELOAD"] = True
gunicorn_logger = logging.getLogger('gunicorn.error')
app.logger.handlers = gunicorn_logger.handlers
app.logger.setLevel(gunicorn_logger.level)
# App is behind one proxy that sets the -For and -Host headers.
app.wsgi_app = ProxyFix(app.wsgi_app)
cache = Cache(app, config={'CACHE_TYPE': 'SimpleCache'})
processing_status = {}
page_headers_key = 'pdf_page_headers_nfr'
pdf_data_key = 'pdf_data_nfr'
pdf_headers_key = 'pdf_headers_nfr'
processing_status_lock = threading.Lock()


def set_processing_status(key, value):
    with processing_status_lock:
        processing_status[key] = value


def get_processing_status(key):
    with processing_status_lock:
        return processing_status.get(key, 'unknown')


@app.route("/")
def index():
    app.logger.debug('index: loading index')
    name = "World!"
    return render_template("index.html", name=name)


@app.route("/nfr")
def nfr():
    app.logger.debug('index: loading nfr')
    # check if the pdf is already being processed
    if get_processing_status('nfr') == 'processing':
        app.logger.debug("PDF is already being processed.")
        return render_template('processing.html', pdf_id='nfr')

    base_url = 'https://www.foodstandards.gov.au'
    page_url = base_url + '/business/novel/novelrecs'

    cached_page_headers = cache.get(page_headers_key)
    cached_pdf_data = cache.get(pdf_data_key)
    cached_pdf_headers = cache.get(pdf_headers_key)

    page_headers = {}
    if cached_page_headers:
        if 'ETag' in cached_page_headers:
            page_headers['If-None-Match'] = cached_page_headers['ETag']

        if 'Last-Modified' in cached_page_headers:
            page_headers['If-Modified-Since'] = cached_page_headers['Last-Modified']

    # Make a conditional request to the server for the page that contains the PDF link
    page_response = requests.get(page_url, headers=page_headers)

    if page_response.status_code == 304 and cached_pdf_data:  # The page hosting the PDF link hasn't changed, use cached data if available
        app.logger.debug("Page hasn't changed, using cached data.")
        return render_template('nfr.html', **cached_pdf_data)
    elif page_response.status_code != 200:
        app.logger.debug("[Error] fetching the page. Status code:", page_response.status_code)
        return render_template('error.html', message="there was problem fetching the page. They said: " + str(page_response.status_code))

    # we have no cache, or the page has changed, so we need to update the cache for the page headers, and check for PDF link headers to see if the PDF has changed

    # Update the cache with the new page headers
    new_page_headers = {
        'ETag': page_response.headers.get('ETag'),
        'Last-Modified': page_response.headers.get('Last-Modified')
    }
    cache.set(page_headers_key, new_page_headers, timeout=30 * 24 * 60 * 60)  # Cache for 30 days

    pdf_url = ''
    soup = BeautifulSoup(page_response.content, 'html.parser')
    link = soup.find('a', class_='file-download-pdf')
    if link:
        pdf_url = base_url + link.get('href')
    else:
        app.logger.debug("[Error] PDF link not found on the page.")
        return render_template('error.html', message="I can't find the PDF link on the page.")

    pdf_headers = {}
    if cached_pdf_headers:
        if 'ETag' in cached_pdf_headers:
            pdf_headers['If-None-Match'] = cached_pdf_headers['ETag']

        if 'Last-Modified' in cached_pdf_headers:
            pdf_headers['If-Modified-Since'] = cached_pdf_headers['Last-Modified']

    # Make a conditional request to the server for the PDF
    pdf_response = requests.get(pdf_url, headers=pdf_headers)
    if pdf_response.status_code == 304 and cached_pdf_data:
        app.logger.debug("PDF hasn't changed, using cached data.")
        return render_template('nfr.html', **cached_pdf_data)
    elif pdf_response.status_code != 200:
        app.logger.debug("[Error] fetching the PDF.")
        return render_template('error.html', message="I couldn't download the PDF.")

    # we got a 200 and the reponse.content is the pdf file
    new_pdf_headers = {
        'ETag': pdf_response.headers.get('ETag'),
        'Last-Modified': pdf_response.headers.get('Last-Modified')
    }
    cache.set(pdf_headers_key, new_pdf_headers, timeout=30 * 24 * 60 * 60)  # Cache for 30 days

    # Start background processing
    set_processing_status('nfr', 'processing')
    threading.Thread(target=process_novel_recs_pdf, args=(pdf_response,)).start()

    return render_template('processing.html', pdf_id='nfr')


@app.route('/check_status/<pdf_id>')
def check_status(pdf_id):
    app.logger.debug(f"Processing status for {pdf_id}: {get_processing_status(pdf_id)}")
    return {'status': get_processing_status(pdf_id)}


def process_novel_recs_pdf(pdf_response):
    try:
        pdf_file_data = io.BytesIO(pdf_response.content)
        pdf_url = pdf_response.url
        pdf_filename = unquote(os.path.basename(pdf_url))

        # read the pdf, flag_size will wrap superscripts with <s></s>
        tables = camelot.read_pdf(pdf_file_data, pages='1-end', flag_size=True)

        # Each table is a dataframe in this list
        df_list = []

        for table in tables:
            df = table.df
            # setup the header, drop the first row
            df.columns = df.iloc[0]
            df = df.drop(0)
#            first_cell_empty = df.iloc[0].iloc[0] == u''
#            second_cell_empty = df.iloc[0].iloc[1] == u''
#            third_cell_empty = df.iloc[0].iloc[2] == u''
#            if (first_cell_empty or second_cell_empty or third_cell_empty):
#                # get last row of prev table (last df in the list)
#                df_prev = df_list[-1]
#                frame_last = df_prev.iloc[-1:]     # last row of the previous table
#                frame_first = df.iloc[0:1]         # first row of the current table
#                # merge the rows
#                frame_last.iloc[0, 0] = frame_last.iloc[0, 0] + ' ' + frame_first.iloc[0, 0]
#                frame_last.iloc[0, 1] = frame_last.iloc[0, 1] + ' ' + frame_first.iloc[0, 1]
#                frame_last.iloc[0, 2] = frame_last.iloc[0, 2] + ' ' + frame_first.iloc[0, 2]
#                # remove the rows we've merged
#                df_prev = df_prev[:-1]
#                df = df[1:]
#                # put the merged row back
#                df_prev = pd.concat([df_prev, frame_last], ignore_index=True)
#                # put df_prev back in the df_list
#                df_list[-1] = df_prev

            # replace superscript numbers with links to the page they appear on
            df = df.replace(to_replace=r'(?<=\W)(\d+)(?=\s*<\/s>)', value=r'<a href="{0}#page={1}"><s>\1</s></a>'.format(pdf_url, table.page), regex=True)
            df_list.append(df)

        frame = pd.concat(df_list, ignore_index=True)
        json_str = frame.to_json()
        json_str = json_str.replace('<s>', '<sup>').replace('<\\/s>', '</sup>')

        # Update the cache with the new processed data
        new_pdf_data = {
            'title': Path(pdf_filename).stem,
            'json': json_str,
            'url': pdf_url
        }

        cache.set(pdf_data_key, new_pdf_data, timeout=30 * 24 * 60 * 60)  # Cache for 30 days

        set_processing_status('nfr', 'complete')
        app.logger.debug("cached new data.")

    except Exception as e:
        app.logger.error(f"Error processing novel_recs PDF: {e}")
        set_processing_status('nfr', f'error: {str(e)}')
