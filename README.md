# google-photos-sync-tool
Python tool to sync your photos matching EXIF keywords to Google Photos albums

Setup:
```
# optionally use virtual environement
virtualenv --python=python3.7 venv
source venv/bin/activate

pip install git+https://github.com/LaurentKol/google-photos-sync-tool.git
```
1. Go to https://console.cloud.google.com/apis/credentials and click "create credentials" and select "OAuth Client Id", once done click 'Download JSON'
2. Put that JSON file in script's directory and rename it.
3. First time you run the script it will open your browser and request permission, once accepted it will create a new json named `credentials.json`
4. You now can use the script 
