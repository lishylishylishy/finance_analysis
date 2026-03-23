import os
import pandas as pd
import yfinance as yf

# Load secret from environment variables
try:
    api_key = os.getenv('API_KEY')
    if not api_key:
        raise ValueError('API Key is not set in environment variables')
except Exception as e:
    print(f'Error loading secrets: {e}')

# Function to download stock data
def download_stock_data(ticker):
    try:
        data = yf.download(ticker)
        if data.empty:
            raise ValueError(f'No data found for ticker: {ticker}')
        return data
    except Exception as e:
        print(f'Error downloading data for {ticker}: {e}')
        return None

# Example usage
if __name__ == '__main__':
    stock_data = download_stock_data('AAPL')
    if stock_data is not None:
        print(stock_data.head())