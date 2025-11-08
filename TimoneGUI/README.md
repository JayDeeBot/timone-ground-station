# TimoneGUI

TimoneGUI is a web-based application built using Flask for the backend and a dynamic frontend. This project aims to provide a user-friendly interface for managing tasks and data.

1. **Run the application**:
   ```bash
   python3 src/app.py
   ```

   Access the application in your web browser at `http://127.0.0.1:5000`.

2. **Run Simulation Tools**:
   ```bash
   python3 tools/log_pusher.py # Sends simulated logs to the Logs Tab

   python3 tools/telemetry_pusher.py # Sends simulated telemetry data to Status and Telemetry Tabs (Graphs and fields should populate)
   
   python3 ~/git/timone-ground-station/TimoneGUI/tools/simulate_embedded.py \
  --flight-log ~/git/timone-ground-station/TimoneGUI/src/data/test_data/goanna_flight_log_remapped.txt \
  --rate-hz 5
   ```

3. **Run the Communication Protocol**:

   Set the correct port for the esp board in ~/git/timone-ground-station/TimoneGUI/tools/sim_port.txt - example: /dev/pts/4

   ```bash
   python3 tools/run_all.py
   ```

4. **Run everything (Including Communication Protocol, Web App and Browser) with the Launcher**

   ```bash
   ./tools/launcher.sh
   ```

5. # Raspberry Pi Login

Username: timone
Password: rocket
Default Keyring Password: rocket

6. # Accessing the Raspberry Pi via SSH (For better performance use the Pi directly with a monitor, keyboard and mouse)

```bash
# Make sure that SSH -X is installed - this will allow you to render the pi's output to your machine
sudo apt update
sudo apt install xorg

# Make sure you are connected to the same wifi - the pi should reconnect to the same network on reboot if its available (first test with monitor)
ssh -X timone@[raspberry-pi-ip] # If you are using hotspot (recommended) then check your connected devices the ip will show up 
# The ip will change everytime you reconnect unless you set a static ip - look into this if you're interested

# Bypass the warning (if it occurs)
yes

# Enter the password
rocket

# If you want to exit
exit
```