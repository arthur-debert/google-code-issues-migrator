#!/usr/bin/env python2
# -*- coding: utf-8 -*-

#
# TODO:
# * code cleanup
# * add attachments for issue body?
#

from __future__ import print_function

import json
import csv
import optparse
import re
import os
import sys
import urllib2

from datetime import datetime
from time import time
from pyquery import PyQuery as pq

# The maximum number of records to retrieve from Google Code in a single request

GOOGLE_MAX_RESULTS = 25

GOOGLE_ISSUES_URL = 'https://code.google.com/p/{}/issues'
GOOGLE_ISSUES_CSV_URL = (GOOGLE_ISSUES_URL +
        '/csv?can=1&num={}&start={}&sort=id&colspec=' +
        '%20'.join([
            'ID',
            'Type',
            'Status',
            'Owner',
            'Summary',
            'Opened',
            'Closed',
            'Reporter',
            'BlockedOn',
            'Blocking',
            'MergedInto',
        ]))

GOOGLE_URL = GOOGLE_ISSUES_URL +'/detail?id={}'

# Format with (google_project_name, issue nr/re)
GOOGLE_ISSUE_RE_TMPL = r'''(?x)
    (?: (?: (?<= \*\*Blocking:\*\* )
          | (?<= \*\*Blockedon:\*\* )
          | (?<= \*\*Mergedinto:\*\* ) ) \s* (?:{0}:)?
      | https?://code\.google\.com/p/{0}/issues/detail\?id=
      | (?i) issue[ #]* ) ({1})'''

# Mapping from Google Code issue labels to Github labels
LABEL_MAPPING = {
    'Type-Defect' : 'bug',
    'Type-Enhancement' : 'enhancement'
}

# Mapping from Google Code issue states to Github labels
STATE_MAPPING = {
    'valid': 'valid',
    'invalid': 'invalid',
    'duplicate': 'duplicate',
    'wontfix': 'wontfix'
}

def output(string, level=0):
    if options.verbose >= level:
        sys.stdout.write(string)
        sys.stdout.flush()

def write_json(obj, filename):
    with open(filename, "w") as fp:
        json.dump(obj, fp, indent=4, separators=(',', ': '), sort_keys=True)
        fp.write('\n')


def parse_gcode_date(date_text):
    """ Transforms a Google Code date into a more human readable string. """

    try:
        parsed = datetime.strptime(date_text, '%a %b %d %H:%M:%S %Y').isoformat()
        return parsed + "Z"
    except ValueError:
        return date_text


def gt(dt_str):
    return datetime.strptime(dt_str.rstrip("Z"), "%Y-%m-%dT%H:%M:%S")


def valid_email(s):
    email = re.sub(r'^https:\/\/code\.google\.com\/u\/([^/]+)\/$', r'\1', s)
    try:
        int(email)
    except ValueError:
        if re.match(r'^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,4}$', email):
            return email
    return ''


def extract_refs(s):
    r = GOOGLE_ISSUE_RE_TMPL.format(google_project_name, r'[0-9]+')
    return set(map(int, re.findall(r, s)))

def fix_gc_issue_n(s, on, nn):
    r = GOOGLE_ISSUE_RE_TMPL.format(google_project_name, on)
    return re.sub(r, '#'+str(nn), s)

def fixup_refs(s):
    delta = (options.issues_start_from - 1)
    refs = extract_refs(s)
    for ref in refs:
        s = fix_gc_issue_n(s, ref, ref + delta)
    refs = set(ref + delta for ref in refs)
    return s, refs


def reindent(s, n=4):
    return "\n".join((n * " ") + i for i in s.splitlines())

def filter_unicode(s):
    for ch in s:
        if ch >= u"\uffff":
            output(" FIXME: unicode %s" % hex(ord(ch)))
            yield "FIXME: unicode %s" % hex(ord(ch))
        else:
            yield ch

MARKDOWN_DATE = gt('2009-04-20T19:00:00Z')
# markdown:
#    http://daringfireball.net/projects/markdown/syntax
#    http://github.github.com/github-flavored-markdown/
# vs textile:
#    http://txstyle.org/article/44/an-overview-of-the-textile-syntax

def format_message(m, comment_nr=0):
    is_issue = (comment_nr == 0)

    i_tmpl = '"#{}"'
    if options.issues_link:
        i_tmpl += ':' + options.issues_link + '/{}'

    m['body'] = ''.join(filter_unicode(m['body']))
    m['body'], refs = fixup_refs(m['body'])

    if is_issue:
        try:
            oid = m['orig_owner']
            del m['orig_owner']
        except KeyError:
            oid = None

    if gt(m['created_at']) >= MARKDOWN_DATE:
        if m['body'].find("```") >= 0:
            m['body'] = reindent(m['body'])
            output(" FIXME: triple quotes in {} body"
                   .format('issue' if is_issue else 'comment '+comment_nr))
        else:
            m['body'] = "```\r\n" + m['body'] + "\r\n```"
        m['body'] += "\r\n"

        if is_issue:
            m['body'] += ("Original issue for #" + str(m['number']) + ": " +
                          m['link'] + "\r\n" +
                          "Original author: " + m['orig_user'] + "\r\n")
        if refs:
            m['body'] += ("Referenced issues: " +
                              ", ".join("#" + str(i) for i in refs) + "\r\n")
        if is_issue:
            if oid:
                m['body'] += ("Original owner: " + oid + "\r\n")
        else:
            m['body'] += ("Original comment: " + m['link'] + "\r\n")
            m['body'] += ("Original author: " + m['orig_user'] + "\r\n")

    else:
        m['body'] = "bc.. " + m['body'] + "\r\n"

        if is_issue:
            m['body'] += ("\r\n" +
                          "p. Original issue for " +
                          i_tmpl.format(*[str(m['number'])]*2) + ": " +
                          '"' + m['link'] + '":' +
                          m['link'] + "\r\n\r\n" +
                          "p. Original author: " + '"' + m['orig_user'] +
                          '":' + m['orig_user'] + "\r\n")
        if refs:
            m['body'] += ("\r\np. Referenced issues: " +
                          ", ".join(i_tmpl.format(*[str(i)]*2) for i in refs) +
                          "\r\n")
        if is_issue:
            if oid:
                m['body'] += ("\r\np. Original owner: " +
                              '"' + oid + '":' + oid + "\r\n")
        else:
            m['body'] += ("\r\np. Original comment: " + '"' + m['link'] +
                          '":' + m['link'] + "\r\n")
            m['body'] += ("\r\np. Original author: " + '"' + m['orig_user'] +
                          '":' + m['orig_user'] + "\r\n")

    if len(m['body']) >= 65534:
        m['body'] = "FIXME: too long issue body"
        output(" FIXME: too long {} body"
               .format('issue' if is_issue else 'comment '+comment_nr))

    del m['orig_user']
    del m['link']


def add_issue_to_github(issue):
    """ Migrates the given Google Code issue to Github. """

    gid = issue['number']
    gid += (options.issues_start_from - 1)

    output('Exporting issue %d' % gid, level=1)

    issue['number'] = gid
    try:
        issue['milestone'] += (options.milestones_start_from - 1)
    except KeyError:
        pass
    issue['title'] = issue['title'].strip()
    if not issue['title']:
        issue['title'] = "FIXME: empty title"
        output(" FIXME: empty title")
    comments = issue['comments']
    del issue['comments']
    del issue['status']
    try:
        del issue['Cc']
    except KeyError:
        pass
    try:
        if issue['owner']:
            issue['assignee'] = issue['owner']
        del issue['owner']
    except KeyError:
        pass
    try:
        del issue['references']
    except KeyError:
        pass

    format_message(issue)
    write_json(issue, "issues/{}.json".format(gid))

    for i, comment in enumerate(comments):
        format_message(comment, i+1)
    write_json(comments, "issues/{}.comments.json".format(gid))


def map_author(gc_userlink, kind=None):
    gc_uid = gc_userlink.text()
    if gc_uid:
        email_pat = gc_uid
        if '@' not in email_pat:
            email_pat += '@gmail.com'
        email_pat = email_pat.replace('.', r'\.').replace(r'\.\.\.@', r'[\w.]+@')
        email_re = re.compile(email_pat, re.I)

        matches = []
        for email, gh_user in authors.items():
            if email_re.match(email):
                matches.append((email, gh_user))
        if len(matches) > 1:
            output('FIXME: multiple matches for {gc_uid}'.format(**locals()))
            for email, gh_user in matches:
                output('\t{email}'.format(**locals()))
        elif matches:
            if options.verbose > 1:
                output("{:<10}    {:>22} -> {:>32}:   {:<16}\n"
                       .format(kind, gc_uid, *matches[0]), level=2)
            return matches[0]

        if options.verbose > 1:
            output("{:<10}!!! {:>22}\n".format(kind, gc_uid), level=2)
        return gc_uid, None

    return None, None

def get_gcode_issue(issue_summary):
    output('Importing issue %d\n' % int(issue_summary['ID']), level=1)

    # Populate properties available from the summary CSV
    issue = {
        'number': int(issue_summary['ID']),
        'title': issue_summary['Summary'].replace('%', '&#37;'),
        'link': GOOGLE_URL.format(google_project_name, issue_summary['ID']),
        'owner': issue_summary['Owner'],
        'state': 'closed' if issue_summary['Closed'] else 'open',
        'created_at': datetime.fromtimestamp(float(issue_summary['OpenedTimestamp'])).isoformat() + "Z",
        'status': issue_summary['Status'].lower(),
        'updated_at': options.updated_at
    }

    refs = set()
    for k in ['BlockedOn', 'Blocking', 'MergedInto']:
        b = issue_summary[k]
        if b:
            refs |= set(map(int, b.split(',')))
    if refs:
        issue['references'] = refs

    global mnum
    global milestones

    ms = ''

    # Build a list of labels to apply to the new issue, including an 'imported' tag that
    # we can use to identify this issue as one that's passed through migration.
    labels = ['imported']
    for label in issue_summary['AllLabels'].split(', '):
        if label.startswith('Priority-') and options.omit_priority:
            continue
        if label.startswith('Milestone-'):
            ms = label[10:]
            continue
        if not label:
            continue
        labels.append(LABEL_MAPPING.get(label, label))

        if ms:
            try:
                milestones[ms]
            except KeyError:
                milestones[ms] = {'number': mnum,
                                  'state': 'open',
                                  'title': ms,
                                  'created_at': issue['created_at']
                                  }
                mnum += 1
            issue['milestone'] = milestones[ms]['number']

    # Add additional labels based on the issue's state
    if issue['status'] in STATE_MAPPING:
        labels.append(STATE_MAPPING[issue['status']])

    issue['labels'] = labels

    # Scrape the issue details page for the issue body and comments
    opener = urllib2.build_opener()
    doc = pq(opener.open(issue['link']).read())

    description = doc('.issuedescription .issuedescription')
    uid, user = map_author(description('.userlink'), 'reporter')
    if uid:
        if user:
            issue['user'] = {'email': user}
        issue['orig_user'] = uid

    # Handle Owner and Cc fields...
    for tr in doc('div[id="meta-float"]')('tr'):
        if pq(tr)('th').filter(lambda i, this: pq(this).text() == 'Owner:'):
            tmp = pq(tr)('.userlink')
            for owner in tmp:
                oid, owner = map_author(pq(owner), 'owner')
                if oid:
                    if owner:
                        issue['owner'] = {'email': owner}
                    issue['orig_owner'] = oid
                    break # only one owner
            break
    for tr in doc('div[id="meta-float"]')('tr'):
        if pq(tr)('th').filter(lambda i, this: pq(this).text() == 'Cc:'):
            tmp = pq(tr)('.userlink')
            if tmp:
                issue['Cc'] = []
            for cc in tmp:
                cid, carbon = map_author(pq(cc), 'cc')
                if cid and carbon:
                    issue['Cc'].append({'email': carbon})
            break

    issue['body'] = description('pre').text()

    issue['comments'] = []
    for comment in doc('.issuecomment'):
        comment = pq(comment)
        if not comment('.date'):
            continue # Sign in prompt line uses same class
        if comment.hasClass('delcom'):
            continue # Skip deleted comments

        date = parse_gcode_date(comment('.date').attr('title'))
        try:
            body = comment('pre').text()
        except UnicodeDecodeError:
            body = u'FIXME: UnicodeDecodeError'
            output("issue %d FIXME: UnicodeDecodeError\n" % (issue['number'] + (options.issues_start_from - 1)))

        uid, user = map_author(comment('.userlink'), 'comment')
        if uid:
            updates = comment('.updates .box-inner')
            if updates:
                body += '\n\n' + updates.html().strip().replace('\n', '').replace('<b>', '**').replace('</b>', '**').replace('<br/>', '\n')

            # Strip the placeholder text if there's any other updates
            body = body.replace('(No comment was entered for this change.)\n\n', '')

            if body.find('**Status:** Fixed') >= 0:
                issue['closed_at'] = date

            if re.match(r'^c([0-9]+)$', pq(comment)('a').attr('name')):
                i = re.sub(r'^c([0-9]+)$', r'\1', pq(comment)('a').attr('name'), flags=re.DOTALL)
            else:
                i = str(len(issue['comments']) + 1)
                output("issue %d FIXME: comment №%d\n" % (issue['number'] + (options.issues_start_from - 1), i))

            c = { 'created_at': date,
                  'user': {'email': user},
                  'body': body,
                  'link': issue['link'] + '#c' + str(i),
                  'orig_user': uid,
                  'updated_at': options.updated_at
                }
            issue['comments'].append(c)

    return issue

def get_gcode_issues():
    count = 100
    start_index = 0
    issues = []
    while True:
        url = GOOGLE_ISSUES_CSV_URL.format(google_project_name, count, start_index)
        issues.extend(row for row in csv.DictReader(urllib2.urlopen(url), dialect=csv.excel))

        if issues and 'truncated' in issues[-1]['ID']:
            issues.pop()
            start_index += count
        else:
            return issues


def process_gcode_issues():
    """ Migrates all Google Code issues in the given dictionary to Github. """

    issues = get_gcode_issues()
    previous_gid = 1

    if options.start_at is not None:
        issues = [x for x in issues if int(x['ID']) >= options.start_at]
        previous_gid = options.start_at - 1
        output('Starting at issue %d\n' % options.start_at, level=1)

    if options.end_at is not None:
        issues = [x for x in issues if int(x['ID']) <= options.end_at]
        output('End at issue %d\n' % options.end_at, level=1)

    for issue in issues:
        issue = get_gcode_issue(issue)

        if options.skip_closed and (issue['state'] == 'closed'):
            continue

        add_issue_to_github(issue)
        output('\n', level=1)

    if milestones:
        for m in milestones.values():
            m['number'] += (options.milestones_start_from - 1)
            with open('milestones/' + str(m['number']) + '.json', 'w') as f:
                output('Adding milestone %d' % m['number'], level=1)
                f.write(json.dumps(m, indent=4, separators=(',', ': '), sort_keys=True))
                f.write('\n')
                output('\n', level=1)


if __name__ == "__main__":
    usage = "usage: %prog [options] <google project name>"
    description = "Export all issues from a Google Code project for a Github project."
    parser = optparse.OptionParser(usage = usage, description = description)

    parser.add_option("-p", "--omit-priority", action = "store_true", dest = "omit_priority",
                      help = "Don't migrate priority labels", default = False)
    parser.add_option('--skip-closed', action = 'store_true', dest = 'skip_closed', help = 'Skip all closed bugs', default = False)
    parser.add_option('--start-at', dest = 'start_at', help = 'Start at the given Google Code issue number', default = None, type = int)
    parser.add_option('--end-at', dest = 'end_at', help = 'End at the given Google Code issue number', default = None, type = int)
    parser.add_option('--issues-start-from', dest = 'issues_start_from', help = 'First issue number', default = 1, type = int)
    parser.add_option('--milestones-start-from', dest = 'milestones_start_from', help = 'First milestone number', default = 1, type = int)
    parser.add_option('--issues-link', dest = 'issues_link', help = 'Full link to issues page in the new repo', default = None, type = str)
    parser.add_option('--export-date', dest = 'updated_at', help = 'Date of export', default = None, type = str)
    parser.add_option("-s", "--silent", action = "store_false", dest = "verbose",
                      help = "Output critical messages only")
    parser.add_option("-v", "--verbose", action = "count", dest = "verbose",
                      help = "Verbosity level (-v to -vvv)", default = 1)

    options, args = parser.parse_args()

    if len(args) != 1:
        parser.print_help()
        sys.exit()

    google_project_name = args[0]

    try:
        with open("authors.json", "r") as f:
            authors = json.load(f)
    except IOError:
        authors = {}

    authors_orig = authors.copy()
    milestones = {}
    mnum = 1
    if not options.updated_at:
        options.updated_at = datetime.fromtimestamp(int(time())).isoformat() + "Z"

    if not os.path.exists('issues'):
        os.mkdir('issues')

    if not os.path.exists('milestones'):
        os.mkdir('milestones')

    try:
        process_gcode_issues()
    except Exception:
        parser.print_help()
        raise

    for k, v in authors.items():
        if k not in authors_orig.keys():
            output('FIXME: NEW AUTHOR %s: %s\n' % (k, v))

    if authors != authors_orig:
        with open("authors.json-new", "w") as f:
            f.write(json.dumps(authors, indent=4,
                               separators=(',', ': '), sort_keys=True))
            f.write('\n')
