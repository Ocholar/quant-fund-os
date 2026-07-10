import time
import os

LOG_FILE = "quant_bot.log"
CHECK_INTERVAL = 600  # Check every 10 minutes
RUN_DURATION = 28800  # 8 hours

def supervise():
    start_time = time.time()
    with open("supervisor_report.log", "w") as f:
        f.write(f"[SUPERVISOR] Monitoring started at {time.ctime()}\n")
    
    while time.time() - start_time < RUN_DURATION:
        time.sleep(CHECK_INTERVAL)
        if os.path.exists(LOG_FILE):
            with open(LOG_FILE, "r") as f:
                logs = f.readlines()
                exits = [l for l in logs if "Executing full exit" in l]
                with open("supervisor_report.log", "a") as rf:
                    rf.write(f"[{time.ctime()}] Full exits: {len(exits)}\n")
        
if __name__ == "__main__":
    supervise()
