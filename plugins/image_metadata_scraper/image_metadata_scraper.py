import os
#from pickle import TRUE
import sys, json
import stashapi.log as log
from stashapi.stashapp import StashInterface
import pathlib
from PIL import Image, ExifTags

def main():
    global stash
    global pattern

    
    json_input = json.loads(sys.stdin.read())
    hookContext = json_input['args'].get("hookContext")
    stash = StashInterface(json_input["server_connection"])
    
    if hookContext and (hookContext.get("type") == "Image.Create.Post"):
        #updateImages(hookContext.get('id'))
        getMetadataFromImage(hookContext.get('id'))
    else:
        getMetadataFromImages()

def get_image_metadata(image_path: pathlib.Path) -> dict:
    """
    Extracts specific EXIF and Photoshop metadata from an image file.

    Args:
        image_path: The pathlib.Path object for the image file.

    Returns:
        A dictionary containing the extracted metadata (XPKeywords, Artist,
        XPComment, Date, Source), or an empty dictionary if no relevant
        metadata is found or an error occurs.
    """
    found_metadata = {}

    try:
        with Image.open(image_path) as img:
            # Helper to decode UTF-16 byte strings from EXIF
            def decode_exif_string(value):
                if isinstance(value, bytes):
                    return value.decode('utf-16-le', errors='ignore').rstrip('\x00')
                return str(value)

            # --- Process EXIF Data ---
            raw_exif = img._getexif()
            exif_data = {}
            if raw_exif is not None:
                exif_data = {
                    ExifTags.TAGS[k]: v
                    for k, v in raw_exif.items() if k in ExifTags.TAGS
                }

            if 'XPKeywords' in exif_data:
                keywords_bytes = exif_data['XPKeywords']
                decoded_keywords = decode_exif_string(keywords_bytes)
                tag_names = [t.strip() for t in decoded_keywords.split(';') if t.strip()]
                found_metadata['XPKeywords'] = ', '.join(tag_names)

            if 'Artist' in exif_data:
                found_metadata['Artist'] = decode_exif_string(exif_data['Artist'])

            if 'XPComment' in exif_data:
                found_metadata['XPComment'] = decode_exif_string(exif_data['XPComment'])

            # --- Find Date ---
            date_tags = ['DateTimeOriginal', 'DateTime', 'DateTimeDigitized']
            for tag in date_tags:
                if tag in exif_data:
                    found_metadata['Date'] = decode_exif_string(exif_data[tag])
                    break  # Print the first one found and stop

            # --- Process Photoshop IRB Data ---
            if img.info and 'photoshop' in img.info:
                photoshop_data = img.info['photoshop']
                if 1035 in photoshop_data: # 1035 is confirmed as Source
                    source_val = photoshop_data[1035]
                    found_metadata['Source'] = source_val.decode('utf-8', errors='ignore') if isinstance(source_val, bytes) else str(source_val)
    except Exception as e:
        print(f"An error occurred while processing {image_path.name}: {e}")
    
    return found_metadata

def get_or_create_tag(tag_name: str) -> dict:
    """
    Finds a tag by name, creating it if it doesn't exist.
    Returns the tag dictionary or None if creation fails.
    """
    tag_name = tag_name.strip()
    if not tag_name:
        return None

    # Check for existing tag
    existing_tags = stash.find_tags(f={"name": {"value": tag_name, "modifier": "EQUALS"}})
    if existing_tags:
        return existing_tags[0]

    # If not found, create it
    log.debug(f"Creating new tag: {tag_name}")
    try:
        new_tag = stash.create_tag({"name": tag_name})
        return new_tag
    except Exception as e:
        log.error(f"Failed to create tag '{tag_name}': {e}")
        return None

def getMetadataFromImage(imageID):
    image = stash.find_image(imageID)
    if image is not None:
        # Safely get the list of files, default to an empty list.
        files = image.get("visual_files", [])
        if not files:
            log.debug(f"Image {imageID} has no visual files. Skipping.")
            return
        log.info("here1")
        # Use the path from the first file.
        image_path = files[0].get("path")
        if not image_path:
            log.debug(f"Image {imageID} file entry has no path. Skipping.")
            return
        log.info("here2")
        path = pathlib.Path(image_path)
        ext = path.suffix.lower()
        try:
            if ext in [".jpg", ".jpeg", ".tiff"]:
                data = get_image_metadata(path)
                if data:
                    update_payload = {"id": imageID}
                    tag_ids_to_add = []
                    performer_ids_to_add = []
                    log.info(f"Extracted metadata for {path.name}: {data}")

                    # Map our extracted keys to the Stash image update fields
                    stash_field_map = {
                        'Source': 'urls',
                        'Artist': 'performer_ids',
                        'XPComment': 'details',
                        'Date': 'date',
                        'XPKeywords': 'tags'
                    }

                    for key, value in data.items():
                        stash_key = stash_field_map.get(key)
                        if not stash_key:
                            continue

                        # Do not overwrite existing data for single-value fields
                        if stash_key not in ['urls', 'performer_ids', 'tags'] and image.get(stash_key):
                            log.debug(f"Skipping '{stash_key}' for image {imageID} as it already has a value.")
                            continue

                        if stash_key == 'urls':
                            # Add to urls list, avoiding duplicates
                            if value not in image.get('urls', []):
                                update_payload[stash_key] = image.get('urls', []) + [value]
                        elif stash_key == 'performer_ids':
                            # Find performer by name and add their ID
                            existing_performer_ids = [p['id'] for p in image.get('performers', [])]
                            performer = stash.find_performer({"name": value}, fragment="id")
                            if performer and performer['id'] not in existing_performer_ids:
                                performer_ids_to_add.append(performer['id'])
                        elif stash_key == 'date':
                            # Format date from "YYYY:MM:DD HH:MM:SS" to "YYYY-MM-DD"
                            update_payload[stash_key] = value.split(" ")[0].replace(":", "-")
                        elif stash_key == 'tags':
                            tag_names = [t.strip() for t in value.split(',') if t.strip()]
                            for tag_name in tag_names:
                                tag = get_or_create_tag(tag_name)
                                if tag:
                                    tag_ids_to_add.append(tag['id'])
                        else:
                            update_payload[stash_key] = value
                    
                    log.info("here4")
                    if len(update_payload) > 1 or tag_ids_to_add or performer_ids_to_add:
                        if tag_ids_to_add:
                            current_tag_ids = [t['id'] for t in image.get('tags', []) if t and 'id' in t]
                            update_payload["tag_ids"] = list(set(current_tag_ids + tag_ids_to_add))
                        if performer_ids_to_add:
                            # Get current performers and append new ones, ensuring no duplicates.
                            current_performer_ids = [p['id'] for p in image.get('performers', []) if p and 'id' in p]
                            update_payload["performer_ids"] = list(set(current_performer_ids + performer_ids_to_add))
                        
                        stash.update_image(update_payload) # This should be safe now
                        log.info(f"Updated image {imageID} ({path.name}) with metadata: {update_payload}") # This should be safe now
        except Exception as e:
            log.error(f"Metadata extraction error for {path}: {e}")

def getMetadataFromImages():
    images = stash.find_images(fragment="id")
    tasks = len(images)
    log.info(f"Found {tasks} images")
    prog = 0
    for image_data in images:
        if image_data and "id" in image_data:
            getMetadataFromImage(image_data["id"])
        prog += 1
        log.progress(prog / tasks)


if __name__ == "__main__":
    main()