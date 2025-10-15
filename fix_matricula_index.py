import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

from mongoengine import connect
from config import Config
from pymongo import MongoClient

# Connect to MongoDB
client = MongoClient(Config.MONGODB_URI)
db = client.get_database()

print("=== FIXING MATRICULA INDEX ===\n")

# Get users collection
users_collection = db['users']

# List current indexes
print("Current indexes on 'users' collection:")
for index in users_collection.list_indexes():
    print(f"  - {index['name']}: {index.get('key', {})}, sparse={index.get('sparse', False)}")

# Drop the old matricula index if it exists
try:
    users_collection.drop_index('matricula_1')
    print("\n✓ Dropped old 'matricula_1' index")
except Exception as e:
    print(f"\nℹ No old index to drop: {e}")

# The sparse index will be created automatically when the app starts
print("\n✓ The new sparse index will be created automatically by MongoEngine")
print("\nDone! Restart the application.")
