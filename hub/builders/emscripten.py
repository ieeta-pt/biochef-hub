import subprocess
import os
import shutil

def build(tool_name, recipe_dir, emscripten_settings, source, output_dir="build"):
    repo_url, tag, commit = source
    
    subprocess.run(["git", "clone", repo_url, tool_name], check=True)
    
    base_dir = os.getcwd()
    os.chdir(tool_name)
    
    if tag:
        subprocess.run(["git", "checkout", "tags/" + tag], check=True)
    elif commit:
        subprocess.run(["git", "checkout", commit], check=True)

    # NOTE(Andrade) 
    # this should probably be somewhere else instead of being hardcoded here
    # not sure if it should be in this repository or in the recipe repository
    # having it here makes it so people can compile an individual recipe without the recipes repo
    # but having it here also makes it harded for people creating the recipe to know which em flags are being used
    env = os.environ.copy()
    env["EM_FLAGS"] = "-s USE_ZLIB=1 -s INVOKE_RUN=0 -s FORCE_FILESYSTEM=1 -s EXPORTED_RUNTIME_METHODS=['callMain','FS','PROXYFS','WORKERFS'] -s MODULARIZE=1 -s ENVIRONMENT=['web','worker'] -s ALLOW_MEMORY_GROWTH=1 -s EXIT_RUNTIME=1 -lworkerfs.js -lproxyfs.js"

    try:
        build_script = emscripten_settings["buildScript"]
        
        shutil.copy(f"{recipe_dir}/{build_script}", ".")
        subprocess.run(f"./{build_script}", shell=True, check=True, env=env)
        
        outputDir = emscripten_settings.get('outputDir', '.')
        from_dir = f"{base_dir}/{tool_name}/{outputDir}"
        dest_dir = f"{base_dir}/{output_dir}/{tool_name}"
        shutil.copytree(from_dir, dest_dir, dirs_exist_ok=True)

        return dest_dir
    except subprocess.CalledProcessError as e:
        print(f"Error building with emscripten: {e}")
        return None
    finally:
        # Remove the git repository
        shutil.rmtree(f"{base_dir}/{tool_name}")
        # Return to the correct dir
        os.chdir(base_dir)