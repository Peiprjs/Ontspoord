STAKING_URL = "https://www.arriva.nl/plan-je-reis/staking/"
STORINGEN_URL = "https://www.arriva.nl/plan-je-reis/storingen-en-werkzaamheden/"

import requests
from bs4 import BeautifulSoup
def fetch_html(url):
    try:
        response = requests.get(url)
        response.raise_for_status()
        return response.text
    except requests.RequestException as e:
        print(f"An error occurred while fetching the URL {url}: {e}")
        return None

html_content = fetch_html(STAKING_URL)
if html_content:
    soup = BeautifulSoup(html_content, 'html.parser')
    # Extract relevant staking data (Customize based on the actual HTML structure)
    print("Staking information scraped and ready for processing.")

html_content = fetch_html(STORINGEN_URL)
if html_content:
    soup = BeautifulSoup(html_content, 'html.parser')
    print("Storingen information scraped and ready for processing.")

