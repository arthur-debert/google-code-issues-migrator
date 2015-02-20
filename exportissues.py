#!/usr/bin/env python2
# -*- coding: utf-8 -*-

#
# TODO:
# * code cleanup
# * add attachments for issue body?
#

from __future__ import print_function

import codecs
import json
import csv
import optparse
import re
import os
import sys
import urllib2
import traceback

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
    'Type-Defect'      : 'bug',
    'Type-Enhancement' : 'enhancement',
    'Priority-Critical': 'prio:high',
    'Priority-High'    : 'prio:high',
    'Priority-Medium'  : None,
    'Priority-Low'     : 'prio:low',
}

# Mapping from Google Code issue states to Github labels
STATE_MAPPING = {
    'valid': 'valid',
    'invalid': 'invalid',
    'duplicate': 'duplicate',
    'wontfix': 'wontfix'
}

CLOSED_STATES = [
    'Fixed',
    'Verified',
    'Invalid',
    'Duplicate',
    'WontFix',
    'Done'
]

class Namespace(object):
    """
    Backport of SimpleNamespace() class added in Python 3.3
    """

    def __init__(self, **kwargs):
        super(Namespace, self).__init__()
        self(**kwargs)

    def __call__(self, **kwargs):
        self.__dict__.update(kwargs)

    __hash__ = None
    def __eq__(self, other):
        return self.__dict__ == other.__dict__

    def __repr__(self):
        keys = sorted(self.__dict__)
        items = ("{}={!r}".format(k, self.__dict__[k]) for k in keys)
        return "{}({})".format(type(self).__name__, ", ".join(items))

class ExtraNamespace(Namespace):
    """
    Adds an extra namespace inaccessible through regular __dict__
    """
    __slots__ = 'extra'

    def __init__(self, **kwargs):
        super(ExtraNamespace, self).__init__()
        self.__dict__.update(kwargs)
        self.extra = Namespace()

def write_json(obj, filename):
    def namespace_to_dict(obj):
        if isinstance(obj, Namespace):
            return obj.__dict__
        raise TypeError("{} is not JSON serializable".format(obj))

    with open(filename, "w") as fp:
        json.dump(obj, fp, indent=4, separators=(',', ': '), sort_keys=True,
                  default=namespace_to_dict)
        fp.write('\n')

def output(string, level=0):
    if options.verbose >= level:
        sys.stdout.write(string)
        sys.stdout.flush()


def parse_gcode_date(date_text):
    """ Transforms a Google Code date into a more human readable string. """
    try:
        parsed = datetime.strptime(date_text, '%a %b %d %H:%M:%S %Y').isoformat()
        return parsed + "Z"
    except ValueError:
        return date_text

def timestamp_to_date(timestamp):
    return datetime.fromtimestamp(long(timestamp)).isoformat() + "Z"


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

def format_md_body_lines(paragraphs):
    lines = []
    for text, is_title in paragraphs:
        if is_title:
            text = "\r\n##### " + ' '.join(line.strip() for line in text.splitlines())
        else:
            if "```" not in text:
                text = "```\r\n" + text + "\r\n```"
            else:
                output(" FIXME: triple quotes in {} body: {}"
                       .format('issue' if is_issue else 'comment ' + comment_nr,
                               text))
                text = reindent(text)
        lines.append(text)
    return '\r\n'.join(lines).strip()


def format_md_comment_updates(u):
    lines = []
    emit = lines.append

    if u.orig_owner == '---':
        emit("Unassigned")
    elif u.orig_owner:
        emit("Assigned to {s_orig_owner}")
        s_orig_owner = format_user(u, 'owner')

    if u.status in CLOSED_STATES:
        emit("Closed with status **{u.status}**")
    elif u.status:
        emit("Reopened, status set to **{u.status}**")

    if u.mergedinto == '---':
        emit("Unmerged")
    elif u.mergedinto:
        emit("Merged into **#{u.mergedinto}**")

    if u.old_milestone and u.new_milestone:
        emit("Moved from the **{u.old_milestone}** milestone to **{u.new_milestone}**")
    elif u.old_milestone:
        emit("Removed from the **{u.old_milestone}** milestone")
    elif u.new_milestone:
        emit("Added to the **{u.new_milestone}** milestone")

    s_old_blocking = format_list(u.old_blocking, '**#{}**', ' or ')
    s_new_blocking = format_list(u.new_blocking, '**#{}**', ' and ')
    if s_old_blocking:
        emit("No more blocking {s_old_blocking}")
    if s_new_blocking:
        emit("Blocking {s_new_blocking}")

    s_old_blockedon = format_list(u.old_blockedon, '**#{}**', ' or ')
    s_new_blockedon = format_list(u.new_blockedon, '**#{}**', ' and ')
    if s_old_blockedon:
        emit("No more blocked on {s_old_blockedon}")
    if s_new_blockedon:
        emit("Blocked on {s_new_blockedon}")

    s_new_labels = format_list(u.new_labels, '**`{}`**')
    s_old_labels = format_list(u.old_labels, '**`{}`**')
    s_labels_plural = 's' * (len(u.new_labels) + len(u.old_labels) > 1)
    if s_new_labels and s_old_labels:
        emit("Added {s_new_labels} and removed {s_old_labels} labels")
    elif s_new_labels:
        emit("Added {s_new_labels} label{s_labels_plural}")
    elif s_old_labels:
        emit("Removed {s_old_labels} label{s_labels_plural}")

    return '\r\n'.join('> {}'.format(line) for line in lines).format(**locals())

MARKDOWN_DATE = gt('2009-04-20T19:00:00Z')
# markdown:
#    http://daringfireball.net/projects/markdown/syntax
#    http://github.github.com/github-flavored-markdown/
# vs textile:
#    http://txstyle.org/article/44/an-overview-of-the-textile-syntax

def format_user(ns, kind='user'):
    if not kind.startswith('orig_'):
        user = getattr(ns, kind, None)
        if user:
            return "@".format(user)  # GitHub @mention
        kind = 'orig_' + kind

    if not hasattr(ns, kind):
        ns = ns.extra
    orig_user = getattr(ns, kind)
    return "**{}**{}{}".format(*orig_user.partition('@'))

def format_list(lst, fmt='{}', last_sep=', '):
    lst = map(fmt.format, lst)
    but_tail = ', '.join(lst[:-1])
    last_pair = lst[-1:]
    if but_tail:
        last_pair.insert(0, but_tail)
    return last_sep.join(last_pair)

def format_message(m, comment_nr=0):
    is_issue = (comment_nr == 0)

    i_tmpl = '"#{}"'
    if options.issues_link:
        i_tmpl += ':' + options.issues_link + '/{}'

    m.body, refs = fixup_refs(''.join(filter_unicode(m.body)))

    if gt(m.created_at) >= MARKDOWN_DATE:
        header = footer = ''

        if not m.user:
            header = ("<sup>{} by {}</sup>\r\n"
                      .format('Reported' if is_issue else 'Comment',
                              format_user(m, 'orig_user')))

        msg_id = m.extra.link
        try:
            body = messages[msg_id].strip()
        except KeyError:
            body = messages[msg_id] = format_md_body_lines(m.extra.paragraphs)

        if is_issue:
            if not m.assignee and m.extra.orig_owner:
                footer = ("> Originally assigned to {s_orig_owner}"
                          .format(s_orig_owner=format_user(m, 'orig_owner')))
        else:
            footer = format_md_comment_updates(m.extra.updates)

        def gen_msg_blocks():
            if header: yield header
            if body:   yield body
            if footer: yield footer

        m.body = '\r\n'.join(gen_msg_blocks())

    else:
        m.body = "bc.. " + m.body + "\r\n"

        if is_issue:
            m.body += ("\r\n" +
                       "p. Original issue for " +
                       i_tmpl.format(*[str(m.number)]*2) + ": " +
                       '"' + m.extra.link + '":' +
                       m.extra.link + "\r\n\r\n" +
                       "p. Original author: " + '"' + m.extra.orig_user +
                       '":' + m.extra.orig_user + "\r\n")
        if refs:
            m.body += ("\r\np. Referenced issues: " +
                       ", ".join(i_tmpl.format(*[str(i)]*2) for i in refs) +
                       "\r\n")
        if is_issue:
            if m.extra.orig_owner:
                m.body += ("\r\np. Original owner: " +
                           '"' + m.extra.orig_owner + '":' + m.extra.orig_owner + "\r\n")
        else:
            m.body += ("\r\np. Original comment: " + '"' + m.extra.link +
                       '":' + m.extra.link + "\r\n")
            m.body += ("\r\np. Original author: " + '"' + m.extra.orig_user +
                       '":' + m.extra.orig_user + "\r\n")

    if len(m.body) >= 65534:
        m.body = "FIXME: too long issue body"
        output(" FIXME: too long {} body"
               .format('issue' if is_issue else 'comment '+comment_nr))


def add_issue_to_github(issue):
    """ Migrates the given Google Code issue to Github. """
    output('Exporting issue %d' % issue.number, level=1)

    format_message(issue)
    write_json(issue, "issues/{}.json".format(issue.number))

    for i, comment in enumerate(issue.extra.comments):
        format_message(comment, i+1)
    write_json(issue.extra.comments, "issues/{}.comments.json".format(issue.number))


def map_author(gc_uid, kind=None):
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
        return matches[0][1]

    if options.verbose > 1:
        output("{:<10}!!! {:>22}\n".format(kind, gc_uid), level=2)

    return options.fallback_user

def add_label_get_milestone(label, labels, issue):
    global milestones

    if label.startswith('Priority-') and options.omit_priority:
        return
    if label.startswith('Milestone-'):
        milestone_name = label[10:]
        if not milestone_name:
            return
        try:
            milestone = milestones[milestone_name]
        except KeyError:
            milestone = milestones[milestone_name] = Namespace(
               number     = len(milestones) + options.milestones_start_from,
               title      = milestone_name,
               created_at = issue.created_at)
        return milestone

    label = LABEL_MAPPING.get(label, label)
    if label and label not in labels:
        labels.append(label)

def split_paragraphs(pquery):
    paragraphs = []

    was_title = None
    accum_text = ''
    for paragraph in pquery.contents():
        is_str = isinstance(paragraph, basestring)
        if not is_str:
            paragraph = pq(paragraph)
        text = (paragraph if is_str else paragraph.text())
        if not text:
            continue
        is_title = not is_str and paragraph.is_('b')
        if is_title == was_title:
            accum_text += text
            continue
        if was_title is not None and accum_text.strip():
            paragraphs.append((accum_text.strip(), was_title))
        accum_text = text
        was_title = is_title
    else:
        if was_title is not None and accum_text.strip():
            paragraphs.append((accum_text.strip(), was_title))

    return paragraphs


def get_gcode_comment_updates(issue, updates_pq):
    updates = Namespace(
        orig_owner    = None,
        owner         = None,
        status        = None,
        mergedinto    = None,
        new_milestone = None,
        old_milestone = None,
        new_blockedon = [],
        old_blockedon = [],
        new_blocking  = [],
        old_blocking  = [],
        new_labels    = [],
        old_labels    = [])

    for text, is_title in split_paragraphs(updates_pq):
        if is_title:
            title = text.partition(':')[0]
            continue

        if title in ('Blockedon', 'Blocking', 'Labels'):
            new_lst = updates.__dict__['new_'+title.lower()]
            old_lst = updates.__dict__['old_'+title.lower()]

            for word in text.split():
                is_removed = word.startswith('-')
                if is_removed:
                    word = word[1:]
                lst = (old_lst if is_removed else new_lst)

                if title in ('Blockedon', 'Blocking'):
                    ref_text = word.rpartition(':')[-1]
                    ref = int(ref_text) + (options.issues_start_from - 1)
                    if ref not in lst:
                        lst.append(ref)

                elif title == 'Labels':
                    milestone = add_label_get_milestone(word, lst, issue)
                    if milestone:
                        if is_removed:
                            updates.old_milestone = milestone.title
                        else:
                            updates.new_milestone = milestone.title

            for el in set(old_lst) & set(new_lst):
                old_lst.remove(el)

        if title == 'Owner':
            updates.orig_owner = text
            updates.owner = map_author(text, 'owner')

        elif title == 'Status':
            updates.status = text

        elif title == 'Mergedinto':
            ref_text = text.rpartition(':')[-1]
            if ref_text:
                updates.mergedinto = int(ref_text) + (options.issues_start_from - 1)
            else:
                updates.mergedinto = '---'

    return updates


def get_gcode_comment(issue, comment_pq):
    comment = ExtraNamespace(
        created_at = parse_gcode_date(comment_pq('.date').attr('title')),
        updated_at = options.updated_at)

    comment.extra(
        link       = issue.extra.link + '#' + comment_pq('a').attr('name'),
        updates    = get_gcode_comment_updates(issue, comment_pq('.updates .box-inner')))

    try:
        body = comment_pq('pre').text().strip()
    except UnicodeDecodeError:
        body = u'FIXME: UnicodeDecodeError'
        output("issue %d FIXME: UnicodeDecodeError\n" % issue.number)
    else:
        # Strip the placeholder text if there's any other updates
        if body == '(No comment was entered for this change.)':
            body = ''

    comment.extra.paragraphs = [(body, False)] if body else []
    comment.body = body

    comment.extra.orig_user  = comment_pq('.userlink').text()
    comment.user = map_author(comment.extra.orig_user, 'comment')

    return comment


def get_gcode_issue(issue_summary):
    output('Importing issue %d\n' % int(issue_summary['ID']), level=1)

    # Populate properties available from the summary CSV
    issue = ExtraNamespace(
        number     = int(issue_summary['ID']) + (options.issues_start_from - 1),
        title      = issue_summary['Summary'].replace('%', '&#37;').strip(),
        state      = 'closed' if issue_summary['Closed'] else 'open',
        closed_at  = timestamp_to_date(issue_summary['ClosedTimestamp']) if issue_summary['Closed'] else None,
        created_at = timestamp_to_date(issue_summary['OpenedTimestamp']),
        updated_at = options.updated_at)

    if not issue.title:
        issue.title = "FIXME: empty title"
        output(" FIXME: empty title")

    issue.extra.orig_user = issue_summary['Reporter']
    issue.user = map_author(issue.extra.orig_user, 'reporter')

    issue.extra.orig_owner = issue_summary['Owner']
    if issue.extra.orig_owner:
        issue.assignee = map_author(issue.extra.orig_owner, 'owner')
    else:
        issue.assignee = None

    issue.extra.link = GOOGLE_URL.format(google_project_name, issue_summary['ID'])

    # Build a list of labels to apply to the new issue, including an 'imported' tag that
    # we can use to identify this issue as one that's passed through migration.
    labels = []
    if options.imported_label:
        labels.append(options.imported_label)

    for label in issue_summary['AllLabels'].split(', '):
        milestone = add_label_get_milestone(label, labels, issue)
        if milestone:
            if issue.state == 'open':
                milestone.state = 'open'
            issue.milestone = milestone.number

    # Add additional labels based on the issue's state
    label = STATE_MAPPING.get(issue_summary['Status'].lower())
    if label:
        labels.append(label)

    issue.labels = labels

    # Scrape the issue details page for the issue body and comments
    opener = urllib2.build_opener()
    doc = pq(opener.open(issue.extra.link).read())

    issue_pq = doc('.issuedescription .issuedescription')

    issue.extra.paragraphs = split_paragraphs(issue_pq('pre'))
    issue.body = issue_pq('pre').text()

    issue.extra.comments = []
    for comment_pq in map(pq, doc('.issuecomment')):
        if not comment_pq('.date'):
            continue # Sign in prompt line uses same class
        if comment_pq.hasClass('delcom'):
            continue # Skip deleted comments

        comment = get_gcode_comment(issue, comment_pq)
        issue.extra.comments.append(comment)

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

    for issue_summary in issues:
        issue = get_gcode_issue(issue_summary)

        if options.skip_closed and (issue.state == 'closed'):
            continue

        add_issue_to_github(issue)
        output('\n', level=1)

    if milestones:
        for m in milestones.values():
            output('Adding milestone %d' % m.number, level=1)
            write_json(m, 'milestones/{}.json'.format(m.number))
            output('\n', level=1)


if __name__ == "__main__":
    usage = "usage: %prog [options] <google project name>"
    parser = optparse.OptionParser(usage = usage,
                description = "Export all issues from a Google Code project for a Github project.")

    parser.add_option("-p", "--omit-priority", action = "store_true", dest = "omit_priority",
                      help = "Don't migrate priority labels", default = False)
    parser.add_option('--skip-closed', action = 'store_true', dest = 'skip_closed', help = 'Skip all closed bugs', default = False)
    parser.add_option('--start-at', dest = 'start_at', help = 'Start at the given Google Code issue number', default = None, type = int)
    parser.add_option('--end-at', dest = 'end_at', help = 'End at the given Google Code issue number', default = None, type = int)
    parser.add_option('--issues-start-from', dest = 'issues_start_from', help = 'First issue number', default = 1, type = int)
    parser.add_option('--milestones-start-from', dest = 'milestones_start_from', help = 'First milestone number', default = 1, type = int)
    parser.add_option('--milestone-date-format', dest = 'milestone_date_format', help = 'Format of [date] for milestones from labels.txt', default = '%Y-%m-%d', type = str)
    parser.add_option('--issues-link', dest = 'issues_link', help = 'Full link to issues page in the new repo', default = None, type = str)
    parser.add_option('--export-date', dest = 'updated_at', help = 'Date of export', default = None, type = str)
    parser.add_option('--imported-label', dest = 'imported_label', help = 'A label to mark all imported issues', default = 'imported', type = str)
    parser.add_option('--fallback-user', dest = 'fallback_user', help = 'Default username for unknown users', default = None, type = str)
    parser.add_option('-d', '--dump-messages', action = 'store_true', dest = 'dump', help = 'Dump text into a file used afterwards to override messages', default = False)
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

    try:
        # The labels.txt file has the same format as a list of predefined
        # issues accessible for Google Code project admins
        # at http://code.google.com/p/PROJ/adminIssues
        #
        # Each Google Code label except for Milestone-xxx should map to
        # a corresponding GitHub label. Empty values are used to completely
        # discard a label.
        #
        #   Type-Defect          = bug
        #   Type-Enhancement     = enhancement
        #   Priority-Critical    = prio:high
        #   Priority-High        = prio:high
        #   Priority-Medium      =
        #   Priority-Low         = prio:low
        #
        # Milestones are treated in a different way.
        # The name of a milestone is extracted from the LHS,
        # and the milestone description is taken form the RHS among with
        # an optional date enclosed in square brackets. The format of the date
        # is specified through --milestone-date-format command line argument.
        #
        #   Milestone-v0.1.3     = [2010-05-01] Basic kernel APIs are implemented
        #
        with open("labels.txt", "r") as f:
            for line in f:
                label, description = (s.strip() for s in line.split('=', 1))
                kind, _, value = label.partition('-')

                if kind == 'Milestone':
                    if not value:
                        raise ValueError("Unable to parse milestone name: '{}'".format(label))

                    milestone = milestones[value] = Namespace(
                       number = len(milestones) + options.milestones_start_from,
                       state  = 'closed',  # unless there will be any open issues encountered
                       title  = value)

                    date_match = re.match(r'^\[([^\]]+)\]\s*', description)
                    if date_match:
                        description = description[date_match.end():]
                        if description:
                            milestone.description = description

                        date_text = date_match.group(1)
                        parsed_date = datetime.strptime(date_text, options.milestone_date_format)

                        milestone.due_on = parsed_date.isoformat() + "Z"

                    continue

                if len(description.split()) > 1:
                    output("FIXME: non-singleword GitHub issue label: '{}'\n"
                           .format(description))
                LABEL_MAPPING[label] = description

    except ValueError:
        traceback.print_exc()
    except IOError:
        pass

    if not options.updated_at:
        options.updated_at = datetime.fromtimestamp(int(time())).isoformat() + "Z"

    if not os.path.exists('issues'):
        os.mkdir('issues')

    if not os.path.exists('milestones'):
        os.mkdir('milestones')

    try:
        messages = {}
        msg_id = None
        with codecs.open("messages.txt", "r", encoding='utf-8') as f:
            for line in f:
                if line.startswith('====()===={{}}====[]==== {}\n'.format(GOOGLE_ISSUES_URL)):
                    msg_id = line.split()[1]
                else:
                    messages[msg_id] = messages.get(msg_id, '') + line
    except IOError:
        messages = {}

    try:
        process_gcode_issues()
    except Exception:
        output('\n')
        parser.print_help()
        raise

    if options.dump:
        try:
            os.rename("messages.txt", "~messages.txt")
        except OSError:
            pass
        try:
            with codecs.open("messages.txt", "w", encoding='utf-8') as f:
                for msg_id, body in messages.iteritems():
                    if not msg_id:
                        continue
                    f.write('====()===={{}}====[]==== {}\n'.format(msg_id))
                    f.write(body.strip())
                    f.write('\n\n')
        except IOError:
            pass


    for k, v in authors.items():
        if k not in authors_orig.keys():
            output('FIXME: NEW AUTHOR %s: %s\n' % (k, v))

    if authors != authors_orig:
        with open("authors.json-new", "w") as f:
            f.write(json.dumps(authors, indent=4,
                               separators=(',', ': '), sort_keys=True))
            f.write('\n')
