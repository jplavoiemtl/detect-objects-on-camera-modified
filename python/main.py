
import subprocess
import sys
import time
import os

def main():
    """
    Wrapper script to run the application in a loop.
    If the inner app crashes (exit code 1), it restarts automatically.
    If the inner app exits cleanly (exit code 0), the wrapper exits too.
    """
    script_path = os.path.join(os.path.dirname(__file__), "inner_main.py")
    
    print("[WRAPPER] Starting application supervisor...")
    
    count = 0
    while True:
        count += 1
        print(f"[WRAPPER] Launching application (Instance #{count})...")
        
        # Start the inner process
        # We pass the same arguments that were passed to this script
        try:
            process = subprocess.Popen([sys.executable, script_path] + sys.argv[1:])
            
            # Wait for it to finish
            exit_code = process.wait()
            
            print(f"[WRAPPER] Application exited with code: {exit_code}")
            
            if exit_code == 0:
                print("[WRAPPER] Clean exit detected. Shutting down wrapper.")
                break
            else:
                print("[WRAPPER] Crash or restart requested. Restarting in 1 second...")
                time.sleep(1)
                
        except KeyboardInterrupt:
            print("\n[WRAPPER] Interrupted by user. Exiting.")
            try:
               process.terminate()
            except:
               pass
            break
        except Exception as e:
            print(f"[WRAPPER] Error launching subprocess: {e}")
            time.sleep(5)

if __name__ == "__main__":
    main()
