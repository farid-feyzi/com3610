#!/usr/bin/python3
"""
Shefmine main
"""

import argparse
import git
import json
import languages.language as lang
import os
import pydriller as pd
import re
import tempfile
import time
import vulnerability as vuln
import flawfinder


def search_repository(repo: pd.GitRepository, repo_mining: pd.RepositoryMining):
    """
    Iterate through all commits of the given repository from the given revision (default: active branch)

    :param repo: The Git repository
    :param repo_mining: The RepositoryMining object
    """

    output = {}

    for commit in repo_mining.traverse_commits():
        for vulnerability in vuln.vulnerability_list:
            commit_message = process_commit_message(commit.msg)
            regex_match = vulnerability.regex.search(commit_message)

            if regex_match is not None and commit.hash not in output:
                output[commit.hash] = {}
                output[commit.hash]['message'] = commit.msg
                output[commit.hash]['vulnerabilities'] = []

                # Add vulnerabilities item
                output[commit.hash]['vulnerabilities'].append({
                    'name': vulnerability.name,
                    'match': regex_match.group()
                })

        # Add files changed
        if commit.hash in output:
            for modification in commit.modifications:
                file = modification.old_path if modification.change_type.name is 'DELETE' else modification.new_path
                partial_output = process_diff(repo.parse_diff(modification.diff), file)

                # Only add the file if it has useful code changes (comments already removed)
                if partial_output:
                    if 'files_changed' not in output[commit.hash]:
                        output[commit.hash]['files_changed'] = []

                    output[commit.hash]['files_changed'].append({'file': file, **partial_output})

            # Remove the commit if no changed files are found (no useful code changes)
            if 'files_changed' not in output[commit.hash]:
                output.pop(commit.hash)

    return output


def process_commit_message(message: str) -> str:
    """
    Pre-process the commit message to remove non-content strings

    :param message: The commit message
    :return: The commit message after pre-processing
    """

    return re.sub('git-svn-id.*', '', message).strip()


def process_diff(diff: dict, filename: str) -> dict:
    """
    Given the diff of a file, check if any vulnerable lines of code are added or deleted

    :param diff: The diff dictionary containing added and deleted lines of the file
    :param filename: The filename of the file
    :return: A partial output containing the vulnerable lines of code added or deleted
    """

    output = {}
    _, file_extension = os.path.splitext(filename)

    test_added = [(num, line.strip()) for (num, line) in diff['added']]

    with tempfile.NamedTemporaryFile(mode='w+t') as fp:
        for num, line in test_added:
            fp.writelines(line + '\n')

        fp.seek(0)
        flawfinder.process_c_file(fp.name, None)
        print(flawfinder.hitlist)

    for language in lang.language_list:
        # Only analyse files that are supported
        if file_extension in language.extensions:
            added = [(num, line.strip()) for (num, line) in diff['added'] if line and language.is_not_comment(line)]
            deleted = [(num, line.strip()) for (num, line) in diff['deleted'] if line and language.is_not_comment(line)]

            # Check if any vulnerable lines of code are added
            for num, line in added:
                vulnerability = []

                for rule in language.rule_set.keys():
                    if re.compile(fr'\b{rule}\b').match(line):
                        vulnerability.append(rule)

                if vulnerability:
                    if 'added' not in output:
                        output['added'] = []
                    else:
                        output['added'].append({
                            'line_num': num,
                            'line': line.strip(),
                            'vulnerability': vulnerability
                        })

            # Check if any vulnerable lines of code are deleted
            for num, line in deleted:
                vulnerability = []

                for rule in language.rule_set.keys():
                    if re.compile(fr'\b{rule}\b').match(line):
                        vulnerability.append(rule)

                if vulnerability:
                    if 'deleted' not in output:
                        output['deleted'] = []
                    else:
                        output['deleted'].append({
                            'line_num': num,
                            'line': line.strip(),
                            'vulnerability': vulnerability
                        })
    return output


def output_result(output: dict, path: str):
    """
    Output the result into a JSON file

    :param output: The output dictionary
    :param path: Path (file name) of the output file
    """

    print(f'{"Issues found":<16}: {len(output)}')

    if os.path.isdir(path):
        os.makedirs(os.path.dirname(path), exist_ok=True)

    with open(path, 'w') as outfile:
        json.dump(output, outfile, indent=2)

    print(f'{"Output location":<16}: {os.path.realpath(path)}')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('repo', help='Path or URL of the Git repository')
    parser.add_argument('-b', '--branch', type=str, help='Only analyse the commits in this branch')
    parser.add_argument('-s', '--single', metavar='HASH', type=str, help='Only analyse the provided commit')
    parser.add_argument('-o', '--output', type=str, help='Write the result to the specified file name and path '
                                                         '(Default: as output.json in current working directory)')
    parser.add_argument('--no-merge', action='store_true', help='Do not include merge commits')
    parser.add_argument('--reverse', action='store_false', help='Analyse the commits from oldest to newest')
    args = parser.parse_args()

    try:
        repo = pd.GitRepository(args.repo)
        repo_mining = pd.RepositoryMining(args.repo,
                                          single=args.single,
                                          only_in_branch=args.branch,
                                          only_no_merge=args.no_merge,
                                          reversed_order=args.reverse)

        if args.output:
            output_name, output_extension = os.path.splitext(args.output)

            if output_extension:
                if output_extension == '.json':
                    output_path = args.output
                else:
                    print('shefmine.py: Output file extension has been automatically changed to .json')
                    output_path = os.path.realpath(output_name + '.json')
            else:
                output_path = os.path.realpath(os.path.join(output_name, 'output.json'))
        else:
            output_path = 'output.json'

        start_time = time.time()
        output_result(search_repository(repo, repo_mining), output_path)

        print(f'{"Time taken":<16}: {(time.time() - start_time):.2f} seconds')
    except git.NoSuchPathError:
        print(f"shefmine.py: '{args.repo}' is not a Git repository")
    except git.GitCommandError:
        print(f"shefmine.py: GitCommandError, bad revision '{args.branch}'")
