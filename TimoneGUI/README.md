# TimoneGUI

TimoneGUI is a web-based application built using Flask for the backend and a dynamic frontend. This project aims to provide a user-friendly interface for managing tasks and data.

1. **Run the application**:
   ```bash
   python3 src/app.py
   ```

   Access the application in your web browser at `http://127.0.0.1:5000`.

2. **Run Simulation Tools**:
   ```bash
   python3 log_pusher.py # Sends simulated logs to the Logs Tab

   python3 telemetry_pusher.py # Sends simulated telemetry data to Status and Telemetry Tabs (Graphs and fields should populate)
   
   python3 simulate_embedded.py \ 
  --flight-log "~/git/timone-ground-station/TimoneGUI/src/data/test_data/goanna flight log" \
  --state-file  "~/git/timone-ground-station/TimoneGUI/src/data/test_data/STATE" \
  --rate-hz 5 # Simulates the embedded routine and sends dummy data
   ```

3. **Run the Communication Protocol**:

   Set the correct port for the esp board in ~/git/timone-ground-station/TimoneGUI/tools/sim_port.txt - example: /dev/pts/4

   ```bash
   python3 ~/git/timone-ground-station/TimoneGUI/tools/run_all.py
   ```

# Raspberry Pi Login

Username: timone
Password: rocket
Default Keyring Password: rocket

# To Do List

1. Setup a system (API/Class/Conifg) for importing new modules such as compass onto the AiMBoard - create the compass method as a template:
    Requirements;
        - Name of the device example: "compass"
        - Graphing - bool flag to say whether the device should graph its data
        - Tabbing - bool flag - should the device have its own Tabbing
        - Raw values - should the raw values be displayed

2. Build a gui to test out the sender messages for the comms. Add this as a debug tab in the flask gui - call it pro settings - password protected.

3. Come up with a handshake message for bootup. Send a unique byte (goodmorning); recieve the same uniqe byte back to say everythin is working or an error byte.