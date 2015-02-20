#!/usr/bin/env python

## Based on https://github.com/alexrudnick/migrate-googlecode-issues

from __future__ import print_function

import os

import gdata.projecthosting.client
import gdata.projecthosting.data
import gdata.gauth
import gdata.client
import atom.http_core
import atom.mock_http_core
import atom.core
import gdata.data

import json
import urllib2
import base64
import getpass
import csv
import optparse
import sys

## Based substantially on live_client_test from the Google Code Project Hosting
## API example, available here.
## http://code.google.com/p/gdata-python-client/ ...
## ... source/browse/tests/gdata_tests/projecthosting/live_client_test.py

GOOGLE_ISSUES_URL = 'https://code.google.com/p/{}/issues/csv?can=1&num={}&start={}&colspec=ID%20Type%20Status%20Owner%20Summary%20Opened%20Closed%20Reporter%20BlockedOn%20Blocking&sort=id'

###
### Code to interact with Google Code Project Hosting
###

def get_gcode_issues(google_project_name):
    count = 100
    start_index = 0
    issues = []
    while True:
        url = GOOGLE_ISSUES_URL.format(google_project_name, count, start_index)
        issues.extend(row for row in csv.DictReader(urllib2.urlopen(url), dialect=csv.excel))

        if issues and 'truncated' in issues[-1]['ID']:
            issues.pop()
            start_index += count
        else:
            return issues


def mark_googlecode_issue_migrated(client,
                                   author_name,
                                   project_name,
                                   issue_id,
                                   github_url):
    comment_text = "Migrated to {0}".format(github_url)
    client.update_issue(project_name,
                        issue_id,
                        author=author_name,
                        comment=comment_text)


def main():
    usage = "usage: %prog [options]"
    description = "Mark all exported issues on a Google Code project"
    parser = optparse.OptionParser(usage = usage, description = description)

    parser.add_option('--start-at', dest = 'start_at', help = 'Start at the given Google Code issue number', default = 1, type = int)
    parser.add_option('--end-at', dest = 'end_at', help = 'End at the given Google Code issue number', default = None, type = int)
    parser.add_option('--issues-start-from', dest = 'issues_start_from', help = 'First moved issue number on GitHub', default = 1, type = int)
    parser.add_option('--google-project', dest = 'google_project', help = 'google project name', default = None, type = str)
    parser.add_option('--google-username', dest = 'google_username', help = 'google username', default = None, type = str)
    parser.add_option('--github-org', dest = 'github_org', help = 'github organisation', default = None, type = str)
    parser.add_option('--github-project', dest = 'github_project', help = 'github project', default = None, type = str)

    options, args = parser.parse_args()

    if not all([options.google_project, options.google_username]):
        parser.print_help()
        sys.exit()

    ### The Google Code source project
    google_project_name = options.google_project

    ### Usernames and passwords for Google Code
    google_username = options.google_username
    google_password = getpass.getpass()
    google_name = google_username.split('@')[0]

    ### GitHub configuration
    github_organization = options.github_org if options.github_org else options.google_project
    github_project = options.github_project if options.github_project else options.google_project

    application_name = 'issue migrator'
    client = gdata.projecthosting.client.ProjectHostingClient()
    client.ClientLogin(google_username, google_password, source=application_name)

    issues = [x for x in get_gcode_issues(google_project_name) if int(x['ID']) >= options.start_at]

    delta = options.issues_start_from - 1

    for issue in issues:
        source_issue_id = int(issue['ID'])
        if options.end_at and source_issue_id > options.end_at:
            break
        print("Migrating", source_issue_id)
        github_issue_id = source_issue_id + delta
        new_github_issue_url = ("http://github.com/{0}/{1}/issues/{2}"
                                .format(github_organization,
                                        github_project,
                                        github_issue_id))
        print("Created", new_github_issue_url)
        mark_googlecode_issue_migrated(client,
                                       google_name,
                                       google_project_name,
                                       source_issue_id,
                                       new_github_issue_url)


if __name__ == "__main__":
    main()
