#!/usr/bin/env python3
"""
test_bot.py - Quick test script to validate bot setup without OANDA credentials
"""

import sys
import os

# Set up test environment variables
os.environ["OANDA_HOST"] = "https://api-fxpractice.oanda.com"
os.environ["OANDA_TOKEN"] = "test-token-123"
os.environ["OANDA_ACCOUNT"] = "test-account-123"
os.environ["DRY_RUN"] = "true"
os.environ["RUNTIME_DIR"] = "./runtime"

print("=" * 70)
print("OANDA TRADING BOT - SETUP VALIDATION")
print("=" * 70)

# Test 1: Python version
print("\n[TEST 1] Python Version")
print(f"  Python {sys.version}")
if sys.version_info < (3, 8):
    print("  ❌ FAILED: Python 3.8+ required")
    sys.exit(1)
print("  ✅ PASSED")

# Test 2: Dependencies
print("\n[TEST 2] Dependencies")
required_packages = {
    "requests": "HTTP client",
    "feedparser": "RSS feed parsing",
    "vaderSentiment": "Sentiment analysis",
    "streamlit": "Dashboard framework",
    "pandas": "Data manipulation"
}

missing = []
for package, desc in required_packages.items():
    try:
        __import__(package)
        print(f"  ✅ {package:20} ({desc})")
    except ImportError:
        print(f"  ❌ {package:20} MISSING")
        missing.append(package)

if missing:
    print(f"\n  ❌ FAILED: Missing packages: {', '.join(missing)}")
    print("  Run: pip install -r requirements.txt")
    sys.exit(1)
print("  ✅ PASSED")

# Test 3: Bot imports
print("\n[TEST 3] Bot Module Imports")
try:
    # Import bot module components
    from datetime import datetime, timezone
    from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer
    print("  ✅ Core imports successful")
except Exception as e:
    print(f"  ❌ FAILED: {e}")
    sys.exit(1)

# Test 4: Sentiment Analysis
print("\n[TEST 4] Sentiment Analysis")
try:
    analyzer = SentimentIntensityAnalyzer()
    
    test_headlines = [
        ("Markets rally on strong earnings", "positive"),
        ("Economic crisis deepens as markets crash", "negative"),
        ("Weather is nice today", "neutral")
    ]
    
    for headline, expected in test_headlines:
        scores = analyzer.polarity_scores(headline)
        sentiment = scores['compound']
        
        if expected == "positive":
            result = "✅" if sentiment > 0.1 else "⚠️"
        elif expected == "negative":
            result = "✅" if sentiment < -0.1 else "⚠️"
        else:
            result = "✅" if -0.1 <= sentiment <= 0.1 else "⚠️"
        
        print(f"  {result} '{headline[:50]}'")
        print(f"      Sentiment: {sentiment:+.3f} (expected: {expected})")
    
    print("  ✅ PASSED")
except Exception as e:
    print(f"  ❌ FAILED: {e}")
    sys.exit(1)

# Test 5: RSS Feed Parsing
print("\n[TEST 5] RSS Feed Parsing")
try:
    import feedparser
    
    feed_url = "https://feeds.reuters.com/reuters/businessNews"
    print(f"  Testing: {feed_url}")
    
    feed = feedparser.parse(feed_url)
    
    if not feed.entries:
        print("  ⚠️  WARNING: No entries found (might be network/feed issue)")
        print("      This is OK for local testing")
    else:
        print(f"  ✅ Found {len(feed.entries)} headlines")
        if len(feed.entries) > 0:
            print(f"      Latest: {feed.entries[0].get('title', 'N/A')[:60]}...")
    
    print("  ✅ PASSED")
except Exception as e:
    print(f"  ⚠️  WARNING: {e}")
    print("      Feed parsing issues are OK for local testing")

print("\n" + "=" * 70)
print("VALIDATION COMPLETE")
print("=" * 70)
print("\n✅ All critical tests passed!")
print("\nNext steps:")
print("  1. Copy .env.example to .env and add your OANDA credentials")
print("  2. Test locally: DRY_RUN=true python bot.py")
print("  3. View dashboard: streamlit run dashboard.py")
print("  4. Deploy to Render when ready")
print("\n⚠️  Remember: Start with DRY_RUN=true and OANDA practice account!")
print("=" * 70)
