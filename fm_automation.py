import os
import subprocess
import time
import uuid

class FrameMakerAutomation:
    def __init__(self):
        self.fm_exe = r"C:\Program Files\Adobe\Adobe FrameMaker 2022\FrameMaker.exe"
        self.appdata = os.environ.get("APPDATA")
        self.temp_dir = os.environ.get("TEMP")
        self.jsx_path = os.path.join(self.appdata, "Adobe", "FrameMaker", "17", "startup", "antigravity_helper.jsx")
        self.job_path = os.path.join(self.temp_dir, "fm_job.txt")
        self.debug_log_path = os.path.join(self.temp_dir, "fm_debug.txt")

    def kill_framemaker(self):
        print("Terminating existing FrameMaker processes...")
        os.system("taskkill /f /im FrameMaker.exe 2>nul")
        time.sleep(1.5)

    def remove_lock_files(self, file_path):
        lock_file = file_path + ".lck"
        if os.path.exists(lock_file):
            try:
                os.remove(lock_file)
                print(f"Removed lock file: {lock_file}")
            except Exception as e:
                print(f"Could not remove lock file: {e}")

    def write_jsx_helper(self):
        jsx_content = """#target framemaker

function log(msg) {
    try {
        var tempDir = Folder.temp.fsName;
        var f = new File(tempDir + "/fm_debug.txt");
        f.open("a");
        f.writeln(new Date().toTimeString() + ": " + msg);
        f.close();
    } catch(e) {}
}

function runHelper() {
    log("Starting runHelper...");
    var tempDir = Folder.temp.fsName;
    var jobFile = new File(tempDir + "/fm_job.txt");
    if (!jobFile.exists) {
        log("No job file found. Exiting.");
        return;
    }

    log("Reading job file...");
    jobFile.open("r");
    var action = jobFile.readln();
    var srcPath = jobFile.readln();
    var destPath = jobFile.readln();
    var resultPath = jobFile.readln();
    jobFile.close();
    log("Job read: action=" + action + ", src=" + srcPath + ", dest=" + destPath);

    // Delete job file so we don't loop
    jobFile.remove();
    log("Job file removed.");

    var resultFile = new File(resultPath);
    resultFile.open("w");
    log("Result file opened for writing.");

    try {
        log("Getting open default params...");
        var openParams = GetOpenDefaultParams();
        log("Got open default params.");
        
        function setParam(params, key, val) {
            var idx = GetPropIndex(params, key);
            if (idx >= 0) {
                params[idx].propVal.ival = val;
                log("Set key " + key + " to " + val);
            } else {
                log("Key " + key + " not found.");
            }
        }

        setParam(openParams, Constants.FS_AlertUserAboutFailure, 0); // 0 is false
        setParam(openParams, Constants.FS_OpenFileNotWritable, Constants.FV_DoOK);
        setParam(openParams, Constants.FS_FileIsOldVersion, Constants.FV_DoOK);
        setParam(openParams, Constants.FS_AlertUserAboutUnknownFiles, Constants.FV_DoNo);
        setParam(openParams, Constants.FS_ShowBookErrorLog, Constants.FV_DoNo);
        setParam(openParams, Constants.FS_LockCantBeReset, Constants.FV_DoOK);
        setParam(openParams, Constants.FS_FileIsInUse, Constants.FV_ResetLockAndContinue);
        setParam(openParams, Constants.FS_FontChangedMetric, Constants.FV_DoOK);
        setParam(openParams, Constants.FS_FontNotFoundInCatalog, Constants.FV_DoOK);
        setParam(openParams, Constants.FS_FontNotFoundInDoc, Constants.FV_DoOK);
        setParam(openParams, Constants.FS_LanguageNotAvailable, Constants.FV_DoOK);
        setParam(openParams, Constants.FS_UseRecoverFile, Constants.FV_DoNo);
        setParam(openParams, Constants.FS_UseAutoSaveFile, Constants.FV_DoNo);
        setParam(openParams, Constants.FS_RefFileNotFound, Constants.FV_AllowAllRefFilesUnFindable);
        
        var fObj = new File(srcPath);
        log("Opening file: " + fObj.fsName);
        var retParams = new PropVals();
        var doc = Open(fObj.fsName, openParams, retParams);
        log("Open finished.");

        if (!doc || !doc.ObjectValid()) {
            log("ERROR: Invalid doc.");
            resultFile.writeln("ERROR: Failed to open document " + srcPath);
            return;
        }
        log("Document opened successfully.");

        if (action === "EXPORT") {
            log("Saving as MIF...");
            var params = GetSaveDefaultParams();
            setParam(params, Constants.FS_AlertUserAboutFailure, 0);
            var returnParams = new PropVals();
            var idx = GetPropIndex(params, Constants.FS_FileType);
            params[idx].propVal.ival = Constants.FV_SaveFmtInterchange; // Save as MIF

            var destObj = new File(destPath);
            doc.Save(destObj.fsName, params, returnParams);
            log("SUCCESS: Saved MIF.");
            doc.Close(Constants.FF_CLOSE_MODIFIED);
            resultFile.writeln("SUCCESS");
        } else if (action === "IMPORT") {
            log("Saving as FM...");
            var params = GetSaveDefaultParams();
            setParam(params, Constants.FS_AlertUserAboutFailure, 0);
            var returnParams = new PropVals();
            var idx = GetPropIndex(params, Constants.FS_FileType);
            params[idx].propVal.ival = Constants.FV_SaveFmtBinary170; // Save as FM 2022 (v17)

            var destObj = new File(destPath);
            doc.Save(destObj.fsName, params, returnParams);
            log("SUCCESS: Saved FM.");
            doc.Close(Constants.FF_CLOSE_MODIFIED);
            resultFile.writeln("SUCCESS");
        } else {
            resultFile.writeln("ERROR: Unknown action " + action);
            doc.Close(Constants.FF_CLOSE_MODIFIED);
        }
    } catch (e) {
        log("ERROR: Exception: " + e.message + " on line " + e.line);
        resultFile.writeln("ERROR: Exception occurred: " + e.message);
    } finally {
        resultFile.close();
    }
}

runHelper();
"""
        os.makedirs(os.path.dirname(self.jsx_path), exist_ok=True)
        with open(self.jsx_path, "w", encoding="utf-8") as f:
            f.write(jsx_content)
        print(f"ExtendScript automation helper written to: {self.jsx_path}")

    def run_job(self, action, src_path, dest_path, timeout=60):
        # Generate unique result filename to prevent stale reads
        unique_id = str(uuid.uuid4())[:8]
        result_path = os.path.join(self.temp_dir, f"fm_result_{unique_id}.txt")
        
        # Ensure helper exists
        self.write_jsx_helper()
        
        # Kill running instances to avoid locking and load startup script
        self.kill_framemaker()
        
        # Remove lock file on source/dest files
        self.remove_lock_files(src_path)
        self.remove_lock_files(dest_path)
        
        # Clear previous result if any
        if os.path.exists(result_path):
            try:
                os.remove(result_path)
            except Exception:
                pass
        if os.path.exists(self.debug_log_path):
            try:
                os.remove(self.debug_log_path)
            except Exception:
                pass

        # Normalize paths for ExtendScript
        src_norm = src_path.replace("\\", "/")
        dest_norm = dest_path.replace("\\", "/")
        result_norm = result_path.replace("\\", "/")
        
        with open(self.job_path, "w", encoding="utf-8") as f:
            f.write(f"{action}\n{src_norm}\n{dest_norm}\n{result_norm}\n")
        print(f"Wrote job file: {self.job_path}")

        # Start FrameMaker
        print("Launching FrameMaker to process job...")
        subprocess.Popen([self.fm_exe])
        
        # Wait for result file
        success = False
        start_time = time.time()
        while time.time() - start_time < timeout:
            if os.path.exists(result_path):
                try:
                    if os.path.getsize(result_path) > 0:
                        with open(result_path, "r", encoding="utf-8") as f:
                            res = f.read().strip()
                        if res:
                            print(f"Result received from FrameMaker: {res}")
                            success = (res == "SUCCESS")
                            break
                except IOError:
                    pass
            time.sleep(0.5)

        # Kill FrameMaker when finished to release locks and close app
        self.kill_framemaker()
        
        # Cleanup temporary files
        if os.path.exists(self.job_path):
            try: os.remove(self.job_path)
            except Exception: pass
        if os.path.exists(result_path):
            try: os.remove(result_path)
            except Exception: pass
            
        # Print debug log if job failed
        if not success and os.path.exists(self.debug_log_path):
            print("--- FrameMaker Debug Log ---")
            with open(self.debug_log_path, "r", encoding="utf-8") as f:
                print(f.read())
                
        return success
