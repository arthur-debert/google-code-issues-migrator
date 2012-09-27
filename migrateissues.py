#!/usr/bin/env python

import optparse
import sys
import re
import logging
import getpass

from datetime import datetime

from github import Github
from github import GithubException

import gdata.projecthosting.client
import gdata.projecthosting.data
import gdata.gauth
import gdata.client
import gdata.data

logging.basicConfig(level = logging.ERROR)

# The maximum number of records to retrieve from Google Code in a single request

GOOGLE_MAX_RESULTS = 25

# Mapping from Google Code issue labels to Github tags

LABEL_MAPPING = {
    'Type-Defect' : "bug",
    'Type-Enhancement' : "enhancement"
}

# Mapping from Google Code issue states to Github tags

STATE_MAPPING = {
    'invalid': "invalid",
    'duplicate': "duplicate"
}


def output(string):
    sys.stdout.write(string)
    sys.stdout.flush()


def github_escape(string):

    """ Returns a copy of the string sanitized for use in Github. """

    return string.replace("%", "&#37;")


def github_label(name, color = "FFFFFF"):

    """ Returns the Github label with the given name, creating it if necessary. """

    try: return github_repo.get_label(name)
    except GithubException:
        return github_repo.create_label(name, color)


def parse_gcode_id(id_text):

    """ Returns the numeric part of a Google Code ID string. """

    return re.search("\d+$", id_text).group(0)


def parse_gcode_date(date_text):

    """ Transforms a Google Code date into  """

    parsed = datetime.strptime(date_text, "%Y-%m-%dT%H:%M:%S.000Z")
    return parsed.strftime("%B %d, %Y %H:%M:%S")


def add_issue_to_github(issue):

    """ Migrates the given Google Code issue to Github. """

    gid = parse_gcode_id(issue.id.text)
    state = issue.state.text
    title = github_escape(issue.title.text)
    link = issue.link[1].href
    author = issue.author[0].name.text
    content = issue.content.text
    date = parse_gcode_date(issue.published.text)

    header = "_Original author: %s (%s)_" % (author, date)
    body = github_escape("%s\n\n%s\n\n\n_Original issue: %s_" % (header, content, link))

    output("Adding issue %s" % gid)

    github_issue = None

    if not options.dry_run:

        github_issue = github_repo.create_issue(title, body = body.encode("utf-8"))
        github_issue.edit(state = state)

        # Add an 'imported' tag so it's easy to identify issues that we created

        github_issue.add_to_labels(github_label("imported"))

        # Add additional tags based on the issue's state

        if state in STATE_MAPPING:
            github_issue.add_to_labels(github_label(state))

    # Assigns issues that originally had an owner to the current user

    if issue.owner and options.assign_owner and not options.dry_run:
        github_issue.edit(assignee = github.get_user(github_user.login))

    # Convert Google Code labels to Github tags where possible

    if issue.label:
        output(", adding labels")
        for label in issue.label:
            if label.text.startswith("Priority-") and options.omit_priority:
                continue
            label_text = LABEL_MAPPING.get(label.text, label.text)
            if not options.dry_run:
                github_issue.add_to_labels(github_label(label_text))
            output(".")

    return github_issue


def add_comments_to_issue(github_issue, gcode_issue_id):

    """ Migrates all comments from a Google Code issue to its Github copy. """

    start_index = 1
    max_results = GOOGLE_MAX_RESULTS

    # Retrieve comments in blocks of GOOGLE_MAX_RESULTS until there are none left

    while True:

        query = gdata.projecthosting.client.Query(start_index = start_index, max_results = max_results)
        comments_feed = google.get_comments(google_project_name, gcode_issue_id, query = query)

        # Filter out empty comments

        comments = [ comment for comment in comments_feed.entry if comment.content.text is not None ]

        # Filter out any comments that already exist in Github and are tagged as imported

        existing_comments = github_issue.get_comments()
        existing_comments = [ comment for comment in existing_comments if comment.body[0:5] == "_From" ]
        existing_comments = [ re.sub(r"^_From.+_\n", "", comment.body) for comment in existing_comments ]
        comments = [ comment for comment in comments if comment.content.text not in existing_comments ]

        # Add any remaining comments to the Github issue

        if not comments:
            break
        if start_index == 1:
            output(", adding comments")
        for comment in comments:
            add_comment_to_github(comment, github_issue)
            output(".")
        start_index += max_results


def add_comment_to_github(comment, github_issue):

    """ Adds a single Google Code comment to the given Github issue. """

    gid = parse_gcode_id(comment.id.text)
    author = comment.author[0].name.text
    date = parse_gcode_date(comment.published.text)
    content = comment.content.text

    body = github_escape("_From %s on %s:_\n%s" % (author, date, content))

    logging.info("Adding comment %s", gid)

    if not options.dry_run:
        github_issue.create_comment(body.encode("utf-8"))


def process_gcode_issues(existing_issues):

    """ Migrates all Google Code issues in the given dictionary to Github. """

    start_index = 1
    max_results = GOOGLE_MAX_RESULTS

    while True:

        query = gdata.projecthosting.client.Query(start_index = start_index, max_results = max_results)
        issues_feed = google.get_issues(google_project_name, query = query)

        if not issues_feed.entry:
            break

        for issue in issues_feed.entry:
            gid = parse_gcode_id(issue.id.text)
            if issue.title.text in existing_issues:
                github_issue = existing_issues[issue.title.text]
                output("Not adding issue %s (exists)" % gid)
            else: github_issue = add_issue_to_github(issue)
            if github_issue:
                add_comments_to_issue(github_issue, gid)
            output("\n")
        start_index += max_results


def get_existing_github_issues():

    """ Returns a dictionary of Github issues previously migrated from Google Code.

    The result maps issue titles to their Github issue objects.

    """

    try:

        open_issues = list(github_repo.get_issues(state = "open"))
        closed_issues = list(github_repo.get_issues(state = "closed"))
        issues = open_issues + closed_issues

        # We only care about issues marked as 'imported'; ones that we created

        existing_issues = [ issue for issue in issues if "imported" in [ label.name for label in issue.get_labels() ] ]
        return dict(zip([ str(issue.title) for issue in existing_issues ], existing_issues))
        # return { str(issue.title): issue for issue in existing_issues }  Python 2.7+

    except Exception:
        return {}



if __name__ == "__main__":

    usage = "usage: %prog [options] <google project name> <github username> <github project>"
    description = "Migrate all issues from a Google Code project to a Github project."
    parser = optparse.OptionParser(usage = usage, description = description)

    parser.add_option("-d", "--dry-run", action = "store_true", dest = "dry_run", help = "Don't modify anything on Github", default = False)
    parser.add_option("-a", "--assign-owner", action = "store_true", dest = "assign_owner", help = "Assign owned tickets to the Github user", default = False)
    parser.add_option("-p", "--omit-priority", action = "store_true", dest = "omit_priority", help = "Don't migrate priority labels", default = False)

    options, args = parser.parse_args()

    if len(args) != 3:
        parser.print_help()
        sys.exit()

    google_project_name, github_user_name, github_project = args
    github_password = getpass.getpass("Github password: ")

    google = gdata.projecthosting.client.ProjectHostingClient()
    github = Github(github_user_name, github_password)
    github_user = github.get_user()
    github_repo = github_user.get_repo(github_project)

    try:
        existing_issues = get_existing_github_issues()
        process_gcode_issues(existing_issues)
    except Exception:
        parser.print_help()
        raise
