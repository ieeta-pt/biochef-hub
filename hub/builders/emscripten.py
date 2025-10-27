import subprocess
import os
import shutil

def build(tool_name, emscripten_settings, source, output_dir="build"):
    buildsystem = emscripten_settings.get('buildsystem')

    if buildsystem == "make":
        repo_url, tag, commit = source
        
        subprocess.run(["git", "clone", repo_url, tool_name], check=True)
        
        base_dir = os.getcwd()
        os.chdir(tool_name)
        
        if tag:
            subprocess.run(["git", "checkout", "tags/" + tag], check=True)
        elif commit:
            subprocess.run(["git", "checkout", commit], check=True)

        workdir = emscripten_settings.get("workDir", ".")
        os.chdir(workdir)

        makefile_path = "Makefile"
        backup_file = f"Makefile.bak"
        shutil.copy(makefile_path, backup_file)

        commands = emscripten_settings.get("commands", [])
        for command in commands:
            subprocess.run(command, shell=True, check=True)

        env = os.environ.copy()
        # TODO: get the flags from biowasm instead of hardcoding them here
        env["EM_FLAGS"] = "-s USE_ZLIB=1 -s INVOKE_RUN=0 -s FORCE_FILESYSTEM=1 -s EXPORTED_RUNTIME_METHODS=['callMain','FS','PROXYFS','WORKERFS'] -s MODULARIZE=1 -s ENVIRONMENT=['web','worker'] -s ALLOW_MEMORY_GROWTH=1 -s EXIT_RUNTIME=1 -lworkerfs.js -lproxyfs.js"

        try:
            subprocess.run(f"emmake make {" ".join(emscripten_settings["env"])}", shell=True, check=True)
            from_dir = f"{base_dir}/{tool_name}/{emscripten_settings['outputDir']}"
            dest_dir = f"{base_dir}/{output_dir}/{tool_name}"
            shutil.copytree(from_dir, dest_dir, dirs_exist_ok=True)

            return dest_dir
        except subprocess.CalledProcessError as e:
            print(f"Error building with emscripten: {e}")
            return None
        finally:
            # Restore the original Makefile from the backup
            shutil.copy(backup_file, makefile_path)
            # Remove the git repository
            shutil.rmtree(f"{base_dir}/{tool_name}")
            # Return to the correct dir
            os.chdir(base_dir)

    return False