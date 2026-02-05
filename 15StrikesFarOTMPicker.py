name: Delta OTM Options Observer
on:
  workflow_dispatch:  # Triggered manually or via API
jobs:
  run-delta-script:
    runs-on: ubuntu-latest
    
    steps:
      - name: Checkout repository
        uses: actions/checkout@v3
      
      - name: Set up Python
        uses: actions/setup-python@v4
        with:
          python-version: '3.10'
      
      - name: Install dependencies
        run: |
          echo "Installing required Python libraries..."
          pip install requests pytz
          echo "Dependencies installed successfully!"
      
      - name: Run Delta OTM Script
        run: |
          echo "=========================================="
          echo "Starting Delta OTM Options Observer"
          echo "Time: $(date)"
          echo "=========================================="
          python 15StrikesFarOTMPicker.py
          EXIT_CODE=$?
          echo "=========================================="
          echo "Script completed at: $(date)"
          echo "Exit code: $EXIT_CODE"
          echo "=========================================="
          if [ $EXIT_CODE -ne 0 ]; then
            echo "ERROR: Script failed with exit code $EXIT_CODE"
            exit $EXIT_CODE
          fi
      
      - name: Check if logs exist
        if: always()
        run: |
          echo "Checking for log files..."
          if [ -d "option_chain_logs" ]; then
            echo "Log directory exists"
            ls -la option_chain_logs/
          else
            echo "ERROR: option_chain_logs directory not found!"
            exit 1
          fi
      
      - name: Display Log
        if: always()
        run: |
          if [ -f option_chain_logs/*.txt ]; then
            echo "=========================================="
            echo "DISPLAYING LOG CONTENT"
            echo "=========================================="
            cat option_chain_logs/*.txt
          else
            echo "No log files found to display"
          fi
      
      - name: Upload logs
        if: always()
        uses: actions/upload-artifact@v4
        with:
          name: option-chain-logs-${{ github.run_number }}
          path: option_chain_logs/
          retention-days: 90
