#!/usr/bin/env python

import optparse
import sys
import re
import logging
import dateutil.parser

from github2.client import Github

import gdata.projecthosting.client
import gdata.projecthosting.data
import gdata.gauth
import gdata.client
import gdata.data

GITHUB_REQUESTS_PER_SECOND = 0.5
GOOGLE_MAX_RESULTS = 25

logging.basicConfig(level=logging.ERROR)


def output(string):
    sys.stdout.write(string)
    sys.stdout.flush()


def parse_gcode_id(id_text):
    return re.search('\d+$', id_text).group(0)


def add_issue_to_github(issue):
    id = parse_gcode_id(issue.id.text)
    title = issue.title.text
    link = issue.link[1].href
    content = issue.content.text
    date = dateutil.parser.parse(issue.published.text).strftime('%B %d, %Y %H:%M:%S')
    body = '%s\n\n\n_Original issue: %s (%s)_' % (content, link, date)

    output('Adding issue %s' % (id))

    github_issue = None

    if not options.dry_run:
        github_issue = github.issues.open(github_project, title=title, body=body.encode('utf-8'))
        github.issues.add_label(github_project, github_issue.number, "imported")
        if issue.status.text.lower() in "invalid closed fixed wontfix verified done duplicate".lower():
            github.issues.close(github_project, github_issue.number)

    # Add any labels
    if len(issue.label) > 0:
        output(', adding labels')
        for label in issue.label:
            if not options.dry_run:
                github.issues.add_label(github_project, github_issue.number, label.text.encode('utf-8'))
            output('.')

    return github_issue


def add_comments_to_issue(github_issue, gcode_issue_id):
    # Add any comments
    start_index = 1
    max_results = GOOGLE_MAX_RESULTS
    while True:
        comments_feed = google.get_comments(google_project_name, gcode_issue_id, query=gdata.projecthosting.client.Query(start_index=start_index, max_results=max_results))
        comments = filter(lambda c: c.content.text is not None, comments_feed.entry)              # exclude empty comments
        existing_comments = github.issues.comments(github_project, github_issue.number)
        existing_comments = filter(lambda c: c.body[0:5] == '_From', existing_comments)           # only look at existing github comments that seem to have been imported
        existing_comments = map(lambda c: re.sub(r'^_From.+_\n', '', c.body), existing_comments)  # get the existing comments' bodies as they appear in gcode
        comments = filter(lambda c: c.content.text not in existing_comments, comments)            # exclude gcode comments that already exist in github
        if len(comments) == 0:
            break
        if start_index == 1:
            output(', adding comments')
        for comment in comments:
            add_comment_to_github(comment, github_issue)
            output('.')
        start_index += max_results
    output('\n')


def add_comment_to_github(comment, github_issue):
    id = parse_gcode_id(comment.id.text)
    author = comment.author[0].name.text
    date = dateutil.parser.parse(comment.published.text).strftime('%B %d, %Y %H:%M:%S')
    content = comment.content.text
    body = '_From %s on %s:_\n%s' % (author, date, content)

    logging.info('Adding comment %s', id)

    if not options.dry_run:
        github.issues.comment(github_project, github_issue.number, body.encode('utf-8'))


def process_gcode_issues(existing_issues):
    start_index = 1
    max_results = GOOGLE_MAX_RESULTS
    while True:
        issues_feed = google.get_issues(google_project_name, query=gdata.projecthosting.client.Query(start_index=start_index, max_results=max_results))
        if len(issues_feed.entry) == 0:
            break
        for issue in issues_feed.entry:
            id = parse_gcode_id(issue.id.text)
            if issue.title.text in existing_issues.keys():
                github_issue = existing_issues[issue.title.text]
                output('Not adding issue %s (exists)' % (id))
            else:
                github_issue = add_issue_to_github(issue)
            add_comments_to_issue(github_issue, id)
        start_index += max_results


def get_existing_github_issues():
    try:
        existing_issues = github.issues.list_by_label(github_project, "imported")  # only include github issues labeled "imported" in our duplicate checking
        existing_issues = dict(zip([str(i.title) for i in existing_issues], existing_issues))
    except:
        existing_issues = {}
    return existing_issues


if __name__ == "__main__":
    usage = "usage: %prog [options] <google_project_name> <github_api_token> <github_user_name> <github_project>"
    description = "Migrate all issues from a Google Code project to a Github project."
    parser = optparse.OptionParser(usage=usage, description=description)
    parser.add_option('-d', '--dry-run', action="store_true", dest="dry_run", help="Don't modify anything on Github", default=False)
    options, args = parser.parse_args()

    if len(args) != 4:
        parser.print_help()
        sys.exit()

    google_project_name, github_api_token, github_user_name, github_project = args

    google = gdata.projecthosting.client.ProjectHostingClient()
    github = Github(username=github_user_name, api_token=github_api_token, requests_per_second=GITHUB_REQUESTS_PER_SECOND)

    try:
        existing_issues = get_existing_github_issues()
        process_gcode_issues(existing_issues)
    except:
        parser.print_help()
        raise
