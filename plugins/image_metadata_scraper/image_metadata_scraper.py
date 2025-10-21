#from PythonDepManager import ensure_import
#ensure_import("piexif==1.1.3")
#ensure_import("stashapi:stashapp-tools>=0.2.58")
import os
#from pickle import TRUE
import sys, json
import uuid
import stashapi.log as log
from stashapi.stashapp import StashInterface
import pathlib
import piexif
import subprocess
from PIL import Image, PngImagePlugin

def main():
    global stash
    global pattern

    
    json_input = json.loads(sys.stdin.read())
    hookContext = json_input['args'].get("hookContext")
    stash = StashInterface(json_input["server_connection"])
    mode_arg = json_input["args"]["mode"]
    if hookContext and (hookContext.get("type") == "Image.Create.Post") and (hookContext.get("date") is None):
        #updateImages(hookContext.get('id'))
        getMetadataFromImage(hookContext.get('id'))
    elif mode_arg == "find":
        getMetadataFromImages()

def getMetadataFromImage(imageID):
    image = stash.find_image(imageID)
    if image and image["url"] is None:
        date = None
        if image["visual_files"][0]["path"]:
            path = pathlib.Path(image["visual_files"][0]["path"])
            ext = path.suffix.lower()
            try:
                if ext in [".jpg", ".jpeg", ".tiff"]:
                    # Try EXIF
                    exif_dict = piexif.load(str(path))

                    # dict to match tag name to stash member name
                    tagNameLookup = {
                        "Source": "url",
                        "CreateDate": "date",
                        "Keywords": "tags",
                        "Comment": "details",
                        "Artist": "photographer"
                    }
                    
                    tagsToAdd = [
                        {"id": imageID}
                    ]

                    for tag in tagNameLookup:
                        val = exif_dict["Exif"].get(piexif.ExifIFD.__dict__.get(tag))
                        if val:
                            if tag == "CreateDate":
                                date = val.decode()
                                # convert from : format to -
                                val = date.replace(":", "-")

                            tagsToAdd.append({
                                tagNameLookup[tag]: val.decode()
                            })
            except Exception as e:
                log.info(f"Metadata extraction error: {e}")

            stash.update_image({
                tagsToAdd
            })
            log.info("Updated image "+str(path)+" with metadata")

def getMetadataFromImages():
    images = stash.find_images(f={
        "url": {
            "modifier": "IS_NULL",
            "value": ""
        },
        "galleries": {
            "modifier": "IS_NULL",
            "value": []
        }
        },fragment="id")
    tasks = len(images)
    log.info(f"Found {tasks} images with no url")
    prog = 0
    for id in images:
        getMetadataFromImage(id["id"])
        prog += 1
        log.progress(prog / tasks)


if __name__ == "__main__":
    main()