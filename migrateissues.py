#!/usr/bin/env python

import optparse
import sys
import re
import logging
import getpass

from datetime import datetime

from github import Github
from github import GithubException
from atom.core import XmlElement

import gdata.projecthosting.client
import gdata.projecthosting.data
import gdata.gauth
import gdata.client
import gdata.data

logging.basicConfig(level = logging.ERROR)

# Patch gdata's CommentEntry Updates object to include the merged-into field

class MergedIntoUpdate(XmlElement):
    _qname = gdata.projecthosting.data.ISSUES_TEMPLATE % 'mergedIntoUpdate'
gdata.projecthosting.data.Updates.mergedIntoUpdate = MergedIntoUpdate

# The maximum number of records to retrieve from Google Code in a single request

GOOGLE_MAX_RESULTS = 25

# The minimum number of remaining Github rate-limited API requests before we pre-emptively
# abort to avoid hitting the limit part-way through migrating an issue.

GITHUB_SPARE_REQUESTS = 50

# Mapping from Google Code issue labels to Github labels

LABEL_MAPPING = {
    'Type-Defect' : "bug",
    'Type-Enhancement' : "enhancement"
}

# Mapping from Google Code issue states to Github labels

STATE_MAPPING = {
    'invalid': "invalid",
    'duplicate': "duplicate",
    'wontfix': "wontfix"
}


def output(string):
    sys.stdout.write(string)
    sys.stdout.flush()


def github_label(name, color = "FFFFFF"):

    """ Returns the Github label with the given name, creating it if necessary. """

    try: return label_cache[name]
    except KeyError:
        try: return label_cache.setdefault(name, github_repo.get_label(name))
        except GithubException:
            return label_cache.setdefault(name, github_repo.create_label(name, color))


def parse_gcode_id(id_text):

    """ Returns the numeric part of a Google Code ID string. """

    return int(re.search("\d+$", id_text).group(0))


def parse_gcode_date(date_text):

    """ Transforms a Google Code date into  """

    parsed = datetime.strptime(date_text, "%Y-%m-%dT%H:%M:%S.000Z")
    return parsed.strftime("%B %d, %Y %H:%M:%S")


def should_migrate_comment(comment):

    """ Returns True if the given comment should be migrated to Github, otherwise False.

    A comment should be migrated if it represents a duplicate-merged-into update, or if
    it has a body that isn't the automated 'issue x has been merged into this issue'.

    """

    if comment.content.text:
        if re.match(r"Issue (\d+) has been merged into this issue.", comment.content.text):
            return False
        return True
    elif comment.updates.mergedIntoUpdate:
        return True
    return False


def format_comment(comment):

    """ Returns the Github comment body for the given Google Code comment.

    Most comments are left unchanged, except to add a header identifying their original
    author and post-date.  Google Code's merged-into comments, used to flag duplicate
    issues, are replaced with a little message linking to the parent issue.

    """

    author = comment.author[0].name.text
    date = parse_gcode_date(comment.published.text)
    content = comment.content.text

    if comment.updates.mergedIntoUpdate:
        return "_This issue is a duplicate of #%d_" % (options.base_id + int(comment.updates.mergedIntoUpdate.text))
    else: return "_From %s on %s_\n%s" % (author, date, content)


def add_issue_to_github(issue):

    """ Migrates the given Google Code issue to Github. """

    gid = parse_gcode_id(issue.id.text)
    status = issue.status.text.lower()
    title = issue.title.text
    link = issue.link[1].href
    author = issue.author[0].name.text
    content = issue.content.text
    date = parse_gcode_date(issue.published.text)

    # Github rate-limits API requests to 5000 per hour, and if we hit that limit part-way
    # through adding an issue it could end up in an incomplete state.  To avoid this we'll
    # ensure that there are enough requests remaining before we start migrating an issue.

    if github.rate_limiting[0] < GITHUB_SPARE_REQUESTS:
        raise Exception("Aborting to to impending Github API rate-limit cutoff.")

    # Build a list of labels to apply to the new issue, including an 'imported' tag that
    # we can use to identify this issue as one that's passed through migration.

    labels = ["imported"]

    # Convert Google Code labels to Github labels where possible

    if issue.label:
        for label in issue.label:
            if label.text.startswith("Priority-") and options.omit_priority:
                continue
            labels.append(LABEL_MAPPING.get(label.text, label.text))

    # Add additional labels based on the issue's state

    if status in STATE_MAPPING:
        labels.append(STATE_MAPPING[status])

    # Add the new Github issue with its labels and a header identifying it as migrated

    github_issue = None

    header = "_Original author: %s (%s)_" % (author, date)
    body = "%s\n\n%s\n\n\n_Original issue: %s_" % (header, content, link)

    output("Adding issue %d" % gid)

    if not options.dry_run:
        github_labels = [ github_label(label) for label in labels ]
        github_issue = github_repo.create_issue(title, body = body.encode("utf-8"), labels = github_labels)

    # Assigns issues that originally had an owner to the current user

    if issue.owner and options.assign_owner:
        assignee = github.get_user(github_user.login)
        if not options.dry_run:
            github_issue.edit(assignee = assignee)

    return github_issue


def add_comments_to_issue(github_issue, gid):

    """ Migrates all comments from a Google Code issue to its Github copy. """

    start_index = 1
    max_results = GOOGLE_MAX_RESULTS

    # Retrieve existing Github comments, to figure out which Google Code comments are new

    existing_comments = [ comment.body for comment in github_issue.get_comments() ]

    # Retrieve comments in blocks of GOOGLE_MAX_RESULTS until there are none left

    while True:

        query = gdata.projecthosting.client.Query(start_index = start_index, max_results = max_results)
        comments_feed = google.get_comments(google_project_name, gid, query = query)

        # Filter out empty and otherwise unnecessary comments, unless they contain the
        # 'migrated into' update for a duplicate issue; we'll generate a special Github
        # comment for those.

        comments = [ comment for comment in comments_feed.entry if should_migrate_comment(comment) and format_comment(comment) not in existing_comments ]

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
    body = format_comment(comment)

    logging.info("Adding comment %d", gid)

    if not options.dry_run:
        github_issue.create_comment(body.encode("utf-8"))


def process_gcode_issues(existing_issues):

    """ Migrates all Google Code issues in the given dictionary to Github. """

    start_index = 1
    previous_gid = 0
    max_results = GOOGLE_MAX_RESULTS

    while True:

        query = gdata.projecthosting.client.Query(start_index = start_index, max_results = max_results)
        issues_feed = google.get_issues(google_project_name, query = query)

        if not issues_feed.entry:
            break

        for issue in issues_feed.entry:

            gid = parse_gcode_id(issue.id.text)

            # If we're trying to do a complete migration to a fresh Github project, and
            # want to keep the issue numbers synced with Google Code's, then we need to
            # watch out for the fact that Google Code sometimes skips issue IDs.  We'll
            # work around this by adding dummy issues until the numbers match again.

            if options.synchronize_ids and previous_gid + 1 < gid:
                while previous_gid + 1 < gid:
                    output("Using dummy entry for missing issue %d\n" % (previous_gid + 1))
                    title = "Google Code skipped issue %d" % (previous_gid + 1)
                    if title not in existing_issues:
                        body = "_Skipping this issue number to maintain synchronization with Google Code issue IDs._"
                        github_issue = github_repo.create_issue(title, body = body, labels = [github_label("imported")])
                        github_issue.edit(state = "closed")
                    previous_gid += 1

            # Add the issue and its comments to Github, if we haven't already

            if issue.title.text in existing_issues:
                github_issue = existing_issues[issue.title.text]
                output("Not adding issue %d (exists)" % gid)
            else: github_issue = add_issue_to_github(issue)

            if github_issue:
                add_comments_to_issue(github_issue, gid)
                if github_issue.state != issue.state.text:
                    github_issue.edit(state = issue.state.text)
            output("\n")

            previous_gid = gid

        start_index += max_results


def get_existing_github_issues():

    """ Returns a dictionary of Github issues previously migrated from Google Code.

    The result maps issue titles to their Github issue objects.

    """

    output("Retrieving existing Github issues...\n")

    try:

        open_issues = list(github_repo.get_issues(state = "open"))
        closed_issues = list(github_repo.get_issues(state = "closed"))
        issues = open_issues + closed_issues

        # We only care about issues marked as 'imported'; ones that we created

        output("Retrieved %d issues; identifying ones already migrated...\n" % len(issues))
        existing_issues = [ issue for issue in issues if "imported" in [ label.name for label in issue.get_labels() ] ]
        return dict(zip([ str(issue.title) for issue in existing_issues ], existing_issues))
        # return { str(issue.title): issue for issue in existing_issues }  Python 2.7+

    except Exception:
        return {}



if __name__ == "__main__":

    usage = "usage: %prog [options] <google project name> <github username> <github project>"
    description = "Migrate all issues from a Google Code project to a Github project."
    parser = optparse.OptionParser(usage = usage, description = description)

    parser.add_option("-a", "--assign-owner", action = "store_true", dest = "assign_owner", help = "Assign owned issues to the Github user", default = False)
    parser.add_option("-b", "--base-id", type = "int", action = "store", dest = "base_id", help = "Number of issues in Github before migration", default = 0)
    parser.add_option("-d", "--dry-run", action = "store_true", dest = "dry_run", help = "Don't modify anything on Github", default = False)
    parser.add_option("-p", "--omit-priority", action = "store_true", dest = "omit_priority", help = "Don't migrate priority labels", default = False)
    parser.add_option("-s", "--synchronize-ids", action = "store_true", dest = "synchronize_ids", help = "Ensure that migrated issues keep the same ID", default = False)

    options, args = parser.parse_args()

    if len(args) != 3:
        parser.print_help()
        sys.exit()

    label_cache = {}    # Cache Github tags, to avoid unnecessary API requests

    google_project_name, github_user_name, github_project = args
    github_password = getpass.getpass("Github password: ")

    google = gdata.projecthosting.client.ProjectHostingClient()
    github = Github(github_user_name, github_password)
    github_user = github.get_user()

    # If the project name is specified as owner/project, assume that it's owned by either
    # a different user than the one we have credentials for, or an organization.

    if "/" in github_project:
        owner_name, github_project = github_project.split("/")
        try: github_owner = github.get_user(owner_name)
        except GithubException:
            try: github_owner = github.get_organization(owner_name)
            except GithubException:
                github_owner = github_user
    else: github_owner = github_user

    github_repo = github_owner.get_repo(github_project)

    try:
        existing_issues = get_existing_github_issues()
        process_gcode_issues(existing_issues)
    except Exception:
        parser.print_help()
        raise
