#!/usr/bin/env python

#
# TODO:
# * adopt https://gist.github.com/izuzak/654612901803d0d0bc3f
# * deal with owner's
# * milestones, labels
# * old issues (before 2009-04-20 19:00:00 UTC) and markdown?
#

from __future__ import print_function
import json
import csv
import getpass
import logging
import optparse
import re
import sys
import urllib2

from datetime import datetime

from pyquery import PyQuery as pq

logging.basicConfig(level = logging.ERROR)

# The maximum number of records to retrieve from Google Code in a single request

GOOGLE_MAX_RESULTS = 25

GOOGLE_ISSUE_TEMPLATE = '_Original issue: {}_'
GOOGLE_ISSUES_URL = 'https://code.google.com/p/{}/issues/csv?can=1&num={}&start={}&colspec=ID%20Type%20Status%20Owner%20Summary%20Opened%20Closed%20Reporter&sort=id'
GOOGLE_URL = 'http://code.google.com/p/{}/issues/detail?id={}'
GOOGLE_URL_RE = 'http://code.google.com/p/%s/issues/detail\?id=(\d+)'
GOOGLE_ID_RE = GOOGLE_ISSUE_TEMPLATE.format(GOOGLE_URL_RE)

# Mapping from Google Code issue labels to Github labels

LABEL_MAPPING = {
    'Type-Defect' : 'bug',
    'Type-Enhancement' : 'enhancement'
}

# Mapping from Google Code issue states to Github labels

STATE_MAPPING = {
    'invalid': 'invalid',
    'duplicate': 'duplicate',
    'wontfix': 'wontfix'
}

MENTIONS_PATTERN = re.compile(r'(.*(?:\s|^))@([a-zA-Z0-9]+\b)')

def output(string):
    sys.stdout.write(string)
    sys.stdout.flush()

def escape(s):
    """Process text to convert markup and escape things which need escaping"""
    if s:
        s = s.replace('%', '&#37;')  # Escape % signs
    return s


def parse_gcode_date(date_text):
    """ Transforms a Google Code date into a more human readable string. """

    try:
        parsed = datetime.strptime(date_text, '%a %b %d %H:%M:%S %Y').isoformat()
        return parsed + "Z"
    except ValueError:
        return date_text


def dereference(matchobj):
    if matchobj.group(1):
        return matchobj.group(1) + "@-" + matchobj.group(2)
    else:
        return "@-" + matchobj.group(2)


def dereferenceMention(content):
    return MENTIONS_PATTERN.sub(dereference, content)


def add_issue_to_github(issue):
    """ Migrates the given Google Code issue to Github. """

    # Github rate-limits API requests to 5000 per hour, and if we hit that limit part-way
    # through adding an issue it could end up in an incomplete state.  To avoid this we'll
    # ensure that there are enough requests remaining before we start migrating an issue.

    body = issue['content'].replace('%', '&#37;')

    output('Adding issue %d' % issue['gid'])

    github_issue = None

    if not options.dry_run:
        github_labels = [github_label(label) for label in issue['labels']]
        milestone = github_milestone(issue['milestone'])
        issue['title'] = issue['title'].strip()
        if issue['title'] == '':
            issue['title'] = "(empty title)"
        github_issue = github_repo.create_issue(issue['title'], body = body.encode('utf-8'), labels = github_labels, milestone = milestone)
    else:
        print(json.dumps(issue, indent=4, separators=(',', ': ')))

    return github_issue


def add_comments_to_issue(github_issue, gcode_issue):
    """ Migrates all comments from a Google Code issue to its Github copy. """

    # Retrieve existing Github comments, to figure out which Google Code comments are new
    existing_comments = [comment.body for comment in github_issue.get_comments()]

    # Add any remaining comments to the Github issue
    output(", adding comments")
    for i, comment in enumerate(gcode_issue['comments']):
        body = u'_From {user} on {date}_\n\n{body}'.format(**comment)
        if body in existing_comments:
            logging.info('Skipping comment %d: already present', i + 1)
        else:
            logging.info('Adding comment %d', i + 1)
            if not options.dry_run:
                github_issue.create_comment(body.encode('utf-8'))
                output('.')
            else:
                print("comment:")
                print(body)


def get_attachments(link, attachments):
    if not attachments:
        return ''

    body = '\n\n'
    for attachment in (pq(a) for a in attachments):
        if not attachment('a'): # Skip deleted attachments
            continue

        # Linking to the comment with the attachment rather than the
        # attachment itself since Google Code uses download tokens for
        # attachments
        body += '**Attachment:** [{}]({})'.format(attachment('b').text().encode('utf-8'), link)
    return body


def get_gcode_issue(issue_summary):
    def get_author(doc):
        userlink = doc('.userlink')
        return '[{}](https://code.google.com{})'.format(userlink.text(), userlink.attr('href'))

    # Populate properties available from the summary CSV
    issue = {
        'gid': int(issue_summary['ID']),
        'title': issue_summary['Summary'].replace('%', '&#37;'),
        'link': GOOGLE_URL.format(google_project_name, issue_summary['ID']),
        'owner': issue_summary['Owner'],
        'state': 'closed' if issue_summary['Closed'] else 'open',
        'date': datetime.fromtimestamp(float(issue_summary['OpenedTimestamp'])).isoformat() + "Z",
        'status': issue_summary['Status'].lower()
    }

    issue['milestone'] = "backlog"

    # Build a list of labels to apply to the new issue, including an 'imported' tag that
    # we can use to identify this issue as one that's passed through migration.
    labels = ['imported']
    for label in issue_summary['AllLabels'].split(', '):
        if label.startswith('Priority-') and options.omit_priority:
            continue
        if label.startswith('Milestone-'):
            issue['milestone'] = label[10:]
            continue
        if not label:
            continue
        labels.append(LABEL_MAPPING.get(label, label))

    # Add additional labels based on the issue's state
    if issue['status'] in STATE_MAPPING:
        labels.append(STATE_MAPPING[issue['status']])

    issue['labels'] = labels

    # Scrape the issue details page for the issue body and comments
    opener = urllib2.build_opener()
    doc = pq(opener.open(issue['link']).read())

    description = doc('.issuedescription .issuedescription')
    uid = 'https://code.google.com{}'.format(description('.userlink').attr('href'))
    try:
        authors_cache[uid]
    except KeyError:
        authors_cache[uid] = uid
    user = authors_cache[uid]
    issue['user'] = {'email': user}

    # Handle Owner and Cc fields...

    for tr in doc('div[id="meta-float"]')('tr'):
        if pq(tr)('th').filter(lambda i, this: pq(this).text() == 'Owner:'):
            tmp = pq(tr)('.userlink')
            for owner in tmp:
                owner = pq(owner).attr('href')
                if owner:
                    oid = 'https://code.google.com{}'.format(owner)
                    try:
                        authors_cache[oid]
                    except KeyError:
                        authors_cache[oid] = oid
                    owner = authors_cache[oid]
                    issue['owner'] = owner
                    break # only one owner
            break

    for tr in doc('div[id="meta-float"]')('tr'):
        if pq(tr)('th').filter(lambda i, this: pq(this).text() == 'Cc:'):
            tmp = pq(tr)('.userlink')
            if tmp:
                issue['Cc'] = []
            for cc in tmp:
                cc = pq(cc).attr('href')
                if cc:
                    cid = 'https://code.google.com{}'.format(cc)
                    try:
                        authors_cache[cid]
                    except KeyError:
                        authors_cache[cid] = cid
                    cc = authors_cache[cid]
                    issue['Cc'].append(cc)
            break

    issue['comments'] = []
    def split_comment(comment, text):
        # Github has an undocumented maximum comment size (unless I just failed
        # to find where it was documented), so split comments up into multiple
        # posts as needed.
        while text:
            comment['body'] = text[:7000]
            text = text[7000:]
            if text:
                comment['body'] += '...'
                text = '...' + text
            issue['comments'].append(comment.copy())

    split_comment(issue, dereferenceMention(description('pre').text()))
    issue['content'] = u'_From {user} on {date}_\n\n{content}{attachments}\n\n{footer}'.format(
            content = issue['comments'].pop(0)['body'],
            footer = GOOGLE_ISSUE_TEMPLATE.format(GOOGLE_URL.format(google_project_name, issue['gid'])),
            attachments = get_attachments(issue['link'], doc('.issuedescription .issuedescription .attachments')),
            **issue)

    issue['comments'] = []
    for comment in doc('.issuecomment'):
        comment = pq(comment)
        if not comment('.date'):
            continue # Sign in prompt line uses same class
        if comment.hasClass('delcom'):
            continue # Skip deleted comments

        date = parse_gcode_date(comment('.date').attr('title'))
        try:
            body = dereferenceMention(comment('pre').text())
        except UnicodeDecodeError:
            body = u'FIXME: unicode err'
            print("unicode err", file=sys.stderr)

        uid = 'https://code.google.com{}'.format(comment('.userlink').attr('href'))
        try:
            authors_cache[uid]
        except KeyError:
            authors_cache[uid] = user
        user = authors_cache[uid]

        updates = comment('.updates .box-inner')
        if updates:
            body += '\n\n' + updates.html().strip().replace('\n', '').replace('<b>', '**').replace('</b>', '**').replace('<br/>', '\n')

        try:
            body += get_attachments('{}#{}'.format(issue['link'], comment.attr('id')), comment('.attachments'))
        except UnicodeDecodeError:
            print("unicode err 2", file=sys.stderr)

        # Strip the placeholder text if there's any other updates
        body = body.replace('(No comment was entered for this change.)\n\n', '')

        split_comment({'date': date, 'user': {'email': user}}, body)

    return issue

def get_gcode_issues():
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


def process_gcode_issues(existing_issues):
    """ Migrates all Google Code issues in the given dictionary to Github. """

    issues = get_gcode_issues()
    previous_gid = 1

    if options.start_at is not None:
        issues = [x for x in issues if int(x['ID']) >= options.start_at]
        previous_gid = options.start_at - 1
        output('Starting at issue %d\n' % options.start_at)

    if options.end_at is not None:
        issues = [x for x in issues if int(x['ID']) <= options.end_at]
        output('End at issue %d\n' % options.end_at)

    for issue in issues:
        issue = get_gcode_issue(issue)

        if options.skip_closed and (issue['state'] == 'closed'):
            continue

        # Add the issue and its comments to Github, if we haven't already
        if issue['gid'] in existing_issues:
            github_issue = existing_issues[issue['gid']]
            output('Not adding issue %d (exists)' % issue['gid'])
        else:
            github_issue = add_issue_to_github(issue)

        if github_issue:
            add_comments_to_issue(github_issue, issue)
            if github_issue.state != issue['state']:
                github_issue.edit(state = issue['state'])
        output('\n')


if __name__ == "__main__":
    usage = "usage: %prog [options] <google project name> <github username> <github project>"
    description = "Export all issues from a Google Code project to a Github project."
    parser = optparse.OptionParser(usage = usage, description = description)

    parser.add_option("-a", "--assign-owner", action = "store_true", dest = "assign_owner",
                      help = "Assign owned issues to the Github user", default = False)
    parser.add_option("-d", "--dry-run", action = "store_true", dest = "dry_run",
                      help = "Don't modify anything on Github", default = False)
    parser.add_option("-p", "--omit-priority", action = "store_true", dest = "omit_priority",
                      help = "Don't migrate priority labels", default = False)
    parser.add_option('--skip-closed', action = 'store_true', dest = 'skip_closed', help = 'Skip all closed bugs', default = False)
    parser.add_option('--start-at', dest = 'start_at', help = 'Start at the given Google Code issue number', default = None, type = int)
    parser.add_option('--end-at', dest = 'end_at', help = 'End at the given Google Code issue number', default = None, type = int)

    options, args = parser.parse_args()

    if len(args) != 1:
        parser.print_help()
        sys.exit()

    google_project_name = args[0]

    try:
        f = open("authors.json", "r")
        authors_cache = json.load(f)
        f.close()
    except IOError:
        authors_cache = {}

    authors_cache_orig = authors_cache.copy()

    label_cache = {} # Cache Github tags, to avoid unnecessary API requests
    milestone_cache = {}
    milestone_number = {}

    try:
        existing_issues = []
        process_gcode_issues(existing_issues)
    except Exception:
        parser.print_help()
        raise

    d = {}
    for v, k in authors_cache.items():
        for v2, k2 in authors_cache.items():
            if v == v2:
                continue
            if k == k2:
                d[v] = k
                d[v2] = k2
    if d:
        print("XXX: duplicates in authors_cache, d = %s" % json.dumps(d, indent=4, separators=(',', ': ')))

    for k, v in authors_cache.items():
        if k not in authors_cache_orig.keys():
            print("NEW AUTHOR %s: %s" % (k, v))

    f = open("authors.json-new", "w")
    f.write(json.dumps(authors_cache, indent=4, separators=(',', ': '), sort_keys=True))
    f.close()
