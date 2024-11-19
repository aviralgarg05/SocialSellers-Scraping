import flask
import tweepy
import requests
from PIL import Image
import pytesseract
from io import BytesIO
import re
from flask import request, jsonify
import google.generativeai as genai
import time
from dotenv import load_dotenv
import os

app = flask.Flask(__name__)

# Load environment variables from .env file
load_dotenv()

# Fetch API keys from environment variables
TWITTER_BEARER_TOKEN = os.getenv('TWITTER_BEARER_TOKEN')
GENAI_API_KEY = os.getenv('GENAI_API_KEY')

# Set up Google Gemini API
genai.configure(api_key=GENAI_API_KEY)

def extract_text_from_image(image_url):
    try:
        response = requests.get(image_url, timeout=10)
        image = Image.open(BytesIO(response.content))
        return pytesseract.image_to_string(image).strip() if image else ""
    except Exception:
        return ""

def generate_content_with_gemini(prompt):
    try:
        # Generate content using the Gemini API
        response = genai.generate(
            model="gemini-1.5-flash",  # Specify the model
            prompt=prompt,
            max_output_tokens=150  # Set the maximum output tokens (adjust as needed)
        )
        return response['text']
    except Exception as e:
        return f"Error generating content: {e}"

def fetch_twitter_post(post_url):
    headers = {
        "Authorization": f"Bearer {TWITTER_BEARER_TOKEN}"
    }
    retries = 5
    backoff_time = 5  # start with 5 seconds backoff

    while retries > 0:
        try:
            tweet_id = re.search(r"status/(\d+)", post_url).group(1)
            tweet_url = f"https://api.twitter.com/2/tweets/{tweet_id}?tweet.fields=public_metrics,attachments&expansions=attachments.media_keys&media.fields=url"
            response = requests.get(tweet_url, headers=headers)

            if response.status_code == 429:  # Rate limit exceeded
                reset_time = int(response.headers.get('X-Rate-Limit-Reset', time.time()))
                sleep_time = reset_time - time.time()  # Calculate time until reset
                if sleep_time > 0:
                    print(f"Rate limit exceeded. Sleeping for {sleep_time} seconds.")
                    time.sleep(sleep_time + 1)  # Adding buffer time
                    retries -= 1  # Decrease retry count
                    backoff_time = backoff_time * 2  # Exponential backoff
                    continue  # Retry request after sleep time
                else:
                    print("Rate limit reset")
                    retries = 0  # Exit the loop if rate limit reset time is reached
            elif response.status_code == 200:
                tweet_data = response.json()
                print("Tweet API Response:", tweet_data)

                tweet = tweet_data.get('data', {})
                media_url = None
                if 'includes' in tweet_data and 'media' in tweet_data['includes']:
                    media_url = tweet_data['includes']['media'][0].get('url', None)

                metrics = tweet.get("public_metrics", {"like_count": 0, "retweet_count": 0})
                return {
                    "content": tweet.get("text", "No content available"),
                    "image_url": media_url,
                    "metrics": {
                        "likes": metrics.get('like_count', 0),
                        "shares": metrics.get('retweet_count', 0),
                        "comments": 0
                    },
                    "source_platform": "Twitter"
                }
            else:
                return {"error": f"Error fetching Twitter post: {response.status_code}"}

        except requests.exceptions.RequestException as e:
            print(f"Error in fetching post: {e}")
            retries -= 1
            time.sleep(backoff_time)
            backoff_time *= 2  # Exponential backoff
    return {"error": "Max retries reached. Please try again later."}

def parse_tweet_for_product_details(tweet_content):
    product_details = {
        "title": "",
        "price": "",
        "category": "General",
        "description": "",
        "brand": "Unknown",
        "attributes": []
    }

    # Extract potential product title
    match_title = re.search(r"([A-Za-z0-9\s\-]+)\s+(is now available|now available|for sale|on sale|buy now)", tweet_content, re.IGNORECASE)
    if match_title:
        product_details["title"] = match_title.group(1).strip()

    # Extract price
    match_price = re.search(r"(Rs\.\s?\d{1,3}(?:,\d{3})*(?:\.\d{2})?)|(USD\s?\d+(\.\d{2})?)", tweet_content, re.IGNORECASE)
    if match_price:
        product_details["price"] = match_price.group(0).strip()

    # Extract attributes like colors, materials, etc.
    match_attributes = re.findall(r"(Black|Grey|Blue|Red|Green|Yellow|Gold|Silver|Cotton|Silk|Polyester|Leather|Wool)", tweet_content, re.IGNORECASE)
    if match_attributes:
        product_details["attributes"] = list(set([attr.capitalize() for attr in match_attributes]))

    # Build description based on extracted details
    attributes_text = ', '.join(product_details['attributes']) if product_details['attributes'] else 'Various features available.'
    product_details["description"] = (
        f"{product_details['title']} is now available at a price of {product_details['price']}. "
        f"Features include: {attributes_text}."
    )

    return product_details

def generate_product_listing_from_tweet(tweet_content):
    product_details = parse_tweet_for_product_details(tweet_content)

    # Generate dimensions and weight using Gemini
    dimensions_prompt = f"Generate typical dimensions (height, width, length) for a product category like {product_details['category']}"
    weight_prompt = f"Estimate the typical weight of a product in the category {product_details['category']}"

    # Use Gemini to generate dynamic dimensions and weight
    dimensions = generate_content_with_gemini(dimensions_prompt).strip() or "0 cm x 0 cm x 0 cm"
    weight = generate_content_with_gemini(weight_prompt).strip() or "0 kg"

    # Parsing the generated dimensions into height, width, and length
    dims_match = re.match(r"(\d+)\s?cm\s*x\s*(\d+)\s?cm\s*x\s*(\d+)\s?cm", dimensions)
    if dims_match:
        height, width, length = dims_match.groups()
    else:
        height, width, length = "0", "0", "0"

    # Parse the weight output
    weight_value = re.match(r"(\d+(\.\d{1,2})?)\s?kg", weight)
    weight_kg = weight_value.group(1) if weight_value else "0"

    product_listing = {
        "asin": "DefaultASIN",
        "availability": "In Stock",
        "brand": product_details["brand"],
        "category": product_details["category"],
        "country_of_origin": "Unknown",
        "description": product_details["description"],
        "dimensions": {
            "height": height,
            "length": length,
            "unit": "cm",
            "width": width
        },
        "generated_listing": f"""
## Product Listing: {product_details['title']}

**Title:** {product_details['title']}

**Price:** {product_details['price']}

**Availability:** In Stock

**Brand:** {product_details['brand']}

**Category:** {product_details['category']}

**Attributes:** {', '.join(product_details['attributes']) if product_details['attributes'] else 'N/A'}

**Description:**
{product_details['description']}

**Dimensions (L x W x H):**
{height} cm x {width} cm x {length} cm

**Weight:**
{weight_kg} kg

**Call to Action:**
Buy Now! [Link to Product Page]
""",
        "item_package_quantity": 1,
        "item_weight": {
            "unit": "kg",
            "value": weight_kg
        },
        "keywords": [product_details["title"], product_details["category"], "product", "sale"],
        "price": {
            "amount": product_details["price"],
            "currency": "INR" if "Rs" in product_details["price"] else "USD"
        },
        "title": product_details["title"]
    }
    return product_listing

@app.route('/generate-listing', methods=['POST'])
def generate_listing():
    try:
        tweet_url = request.json.get('tweet_url')
        if not tweet_url:
            return jsonify({"error": "Missing tweet URL"}), 400

        tweet_data = fetch_twitter_post(tweet_url)
        if 'error' in tweet_data:
            return jsonify(tweet_data), 500

        tweet_content = tweet_data["content"]
        gemini_listing = generate_product_listing_from_tweet(tweet_content)
        gemini_listing["social_media_metrics"] = tweet_data["metrics"]

        return jsonify(gemini_listing), 200

    except Exception as e:
        return jsonify({"error": f"Error processing the request: {e}"}), 500

if __name__ == '__main__':
    app.run(debug=True)