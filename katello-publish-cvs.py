#!/usr/bin/python

import json
import sys
import time
from datetime import datetime
import requests
from requests.packages.urllib3.exceptions import InsecureRequestWarning
requests.packages.urllib3.disable_warnings(InsecureRequestWarning)

# URL to your Satellite 6 server
URL = "https://localhost/"
# URL for the API to your deployed Satellite 6 server
SAT_API = URL + "katello/api/v2/"
# Katello-specific API
KATELLO_API = URL + "katello/api/"
POST_HEADERS = {'content-type': 'application/json'}
# Default credentials to login to Satellite 6
USERNAME = "admin"
PASSWORD = "changeme"
# Ignore SSL for now
SSL_VERIFY = False
# Name of the organization to be either created or used
ORG_NAME = "Default Organization"
# Dictionary for Life Cycle Environments ID and name
ENVIRONMENTS = {}
# Search string to list currently running publish tasks
publish_tasks = "foreman_tasks/api/tasks?search=utf8=%E2%9C%93&search=label+%3D+Actions%3A%3AKatello%3A%3AContentView%3A%3APublish+and+state+%3D+running"
sync_tasks = "foreman_tasks/api/tasks?utf8=%E2%9C%93&per_page=1000&search=label+%3D+Actions%3A%3AKatello%3A%3ARepository%3A%3ASync+and+state+%3D+stopped+and+result+%3D+success"
promotion_tasks = "foreman_tasks/api/tasks?search=utf8=%E2%9C%93&search=label+%3D+Actions%3A%3AKatello%3A%3AContentView%3A%3APromote+and+state+%3D+running"

def get_json(location):
    """
    Performs a GET using the passed URL location
    """

    r = requests.get(location, auth=(USERNAME, PASSWORD), verify=SSL_VERIFY)

    return r.json()


def post_json(location, json_data):
    """
    Performs a POST and passes the data to the URL location
    """

    result = requests.post(location,
                            data=json_data,
                            auth=(USERNAME, PASSWORD),
                            verify=SSL_VERIFY,
                            headers=POST_HEADERS)

    return result.json()

def put_json(location, json_data):
    """
    Performs a PUT and passes the data to the URL location
    """

    result = requests.put(location,
                            data=json_data,
                            auth=(USERNAME, PASSWORD),
                            verify=SSL_VERIFY,
                            headers=POST_HEADERS)

    return result.json()

def wait_for_publish(seconds):
    """
    Wait for all publishing tasks to terminate. Search string is:
    label = Actions::Katello::ContentView::Publish and state = running
    """
   
    count = 0 
    print "Waiting for publish tasks to finish..."
    
    # Make sure that publish tasks gets the chance to appear before looking for them
    time.sleep(2) 
    
    while get_json(URL + publish_tasks)["total"] != 0:
        time.sleep(seconds)
        count += 1

    print "Finished waiting after " + str(seconds * count) + " seconds"

def wait_for_promotion(seconds):
    """
    Wait for all publishing tasks to terminate. Search string is:
    label = Actions::Katello::ContentView::Promote and state = running
    """

    count = 0
    print "Waiting for publish tasks to finish..."

    # Make sure that publish tasks gets the chance to appear before looking for them
    time.sleep(2)

    while get_json(URL + promotion_tasks)["total"] != 0:
        time.sleep(seconds)
        count += 1

    print "Finished waiting after " + str(seconds * count) + " seconds"
    
def main():

    # Check that organization exists and extract its ID
    org_json = get_json(SAT_API + "organizations/" + ORG_NAME)
    
    if org_json.get('error', None):
        print "ERROR: Inspect message"
        print org_json
        sys.exit(1)

    org_id =org_json["id"]
    print 'Organization \"' + ORG_NAME + ' has ID: ' + str(org_id)

    # Fill dictionary of Lifecycle Environments as {name : id}
    envs_json = get_json(KATELLO_API + "organizations/" + str(org_id) + "/environments?per_page=999")
    for env in envs_json["results"]:
        ENVIRONMENTS[env["name"]] = env["id"]

    print "Lifecycle environments: " + str(ENVIRONMENTS)
    
    # Get all non-composite CVs from the API
    cvs_json = get_json(SAT_API + "organizations/" + str(org_id) + "/content_views?noncomposite=true&nondefault=true")
   
    # Get all sync tasks
    sync_tasks_json = get_json(URL + sync_tasks)

    # Publish new versions of the CVs that have new content in the underlying repos
    published_cv_ids = []
    for cv in cvs_json["results"]:
        last_published = cv["last_published"]
        if last_published is None:
            last_published = "2000-01-01 00:00:00 UTC"
        last_published = datetime.strptime(last_published, '%Y-%m-%d  %X %Z')

        need_publish = False
        for repo in cv["repositories"]:

            for task in sync_tasks_json["results"]:
                if task["input"]["repository"]["id"] == repo["id"]:
                    ended_at = datetime.strptime(task["ended_at"], '%Y-%m-%dT%H:%M:%S.000Z')

                    if ended_at > last_published and task["input"]["contents_changed"]:
                        print "A sync task for repo \"" + repo["name"] + "\" downloaded new content and ended after " + cv["name"] + " was published last time"
                        need_publish = True

        if need_publish:
            print "Publish " + cv["name"] + " because some of its content has changed"
            post_json(KATELLO_API + "content_views/" + str(cv["id"]) + "/publish", json.dumps({"description": "Automatic publish over API"}))
            published_cv_ids.append(cv["id"])
        else:
            print cv["name"] + " doesn't need to be published"

    wait_for_publish(10)

    # Get all CCVs from the API 
    ccvs_json = get_json(SAT_API + "organizations/" + str(org_id) + "/content_views?composite=true")
    
    # Publish a new version of all CCs that contain any of the published CVs
    ccv_ids_to_promote = []
    for ccv in ccvs_json["results"]:
        new_component_ids = []
        
        for component in ccv["components"]:
            cv_json = get_json(KATELLO_API + "content_views/" + str(component["content_view"]["id"]))
            
            for version in cv_json["versions"]:
                if ENVIRONMENTS["Library"] in version["environment_ids"]:
                    new_component_ids.append(version["id"])
        
        print "Update " + ccv["name"] + " with new compontent IDs: " + str(new_component_ids)
        put_json(KATELLO_API + "content_views/" + str(ccv["id"]), json.dumps({"component_ids": new_component_ids}))
        
        print "Publish new version of " + ccv["name"]
        post_json(KATELLO_API + "content_views/" + str(ccv["id"]) + "/publish", json.dumps({"description": "Automatic publish over API"}))

        # Get the ID of the version in Library 
        version_in_library_id = get_json(KATELLO_API + "content_views/" + str(ccv["id"]) + "/content_view_versions?environment_id=" + str(ENVIRONMENTS["Library"]))["results"][0]["id"]
        ccv_ids_to_promote.append(str(version_in_library_id))

    wait_for_publish(10)
    
    print "Promote all effected CCVs to TEST environment"
    for ccv_id in ccv_ids_to_promote:
        post_json(KATELLO_API + "content_view_versions/" + str(ccv_id) + "/promote", json.dumps({"environment_id": ENVIRONMENTS["TEST"]})) 
        
    wait_for_promotion(10)

    for ccv_id in ccv_ids_to_promote:
        post_json(KATELLO_API + "content_view_versions/" + str(ccv_id) + "/promote", json.dumps({"environment_id": ENVIRONMENTS["PROD"]}))

if __name__ == "__main__":
    main()
