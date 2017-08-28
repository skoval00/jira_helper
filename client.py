#!/usr/bin/env python
# encoding: utf-8
"""
Доки по используемым компонентам
===
* jira-python
  https://jira.readthedocs.io
* бот для аськи
  https://gitlab.corp.mail.ru/android-im-components/jprotolib

Набор инструментов для повседневной работы с жирой
* Получение списка тасков для релиза из определенной ветки
* Выявление готовности тасков к тестированию и релизу
* Перевод списка тасков в определенное состояние
* Создание тасков на подготовку, тестирование и деплой релиза

Работа начинается с того,
что в мастере скапливается некоторое количество готовых тасков

Я пытаюсь сделать так чтобы командой релиз на любом этапе
просто делалось всё, что нужно

Типа пытаемся взять таск, если нет, делаем
Появились новые задачи, долинковываем
Есть таска на раскладку, закрываем


Релиз
===
* Формируем таск на подготовку релиза
* Формируем таск на тестирование релиза
* Связываем эти таски
* Собираем таски до появления коммита мажорной версии
* Отфильтровываем такси, которые возможно вошли в другой релиз
(Можно проверять, что этих тасков/коммитов нет в rpm-ветке)
* Даём рекомендации по таскам
* Хорошие таски
    * переводим в нужный статус
    * проставляем версию
    * линкуем к таску на тестирование
*
* Если таск на тестирование в определенном статусе,
    * Закрываем таск на подготовку
    * Делаем таск на раскладку

* Обновить имя доски (дописывать)
* Обновить фильтр доски
* Обновить swimlanes доски (дописывать)


Дорелиз
===
*

Когда релиз в продакшене и всё ок

Закрытие релиза
===
* Закрываем таск на раскладку
* Таск на



"""
from __future__ import unicode_literals, absolute_import

import re
import os
import sys
import json
import logging
import ConfigParser
import webbrowser
from datetime import datetime
from collections import defaultdict
import httplib

from jira import JIRA, JIRAError, Issue
from jira.resources import GreenHopperResource, Board
from jira.client import GreenHopper, translate_resource_args
from jira.utils import json_loads
from git import Repo


TASK_TEMPLATE_DICT = {
    # https://jira.mail.ru/browse/BIZ-3703
    'weekly_dev': {
        'template': 'BIZ-3703',
        'jql': 'project = "{project_key}" AND summary ~ "{task[summary]}"',
        'params': {
            'summary': 'Еженедельное улучшение инфраструктуры {number}',
            'description': '\n'.join([
                '* Пункт 1',
                '* Пункт 2',
                '* Пункт 3',
                '* ???',
                '* Профит',
            ]),
            'issuetype': {'name': 'Task'},
            'components': [{'name': 'Server'}],
            'Epic Link': 'BIZ-1816',
        }
    },
    # https://jira.mail.ru/browse/BIZ-3797
    'prepare_release': {
        'template': 'BIZ-3797',
        'jql': '',
        'params': {
            'summary': 'Подготовить релиз {version.name}',
            'description': '\n'.join([
                '* Собрать пререлиз',
                '* Проревьють таски',
                '* Таск на тестирование',
                '* Окончательное ревью и сборка',
                '* Таск на деплой',
            ]),
            'issuetype': {'name': 'Релиз'},
            'components': [{'name': 'Server'}],
        }
    },
    # # https://jira.mail.ru/browse/BIZ-3798
    'test_release': {
        'template': 'BIZ-3798',
        'jql': '',
        'params': {
            'summary': 'Протестировать релиз {version.name}',
            'description': '\n'.join([
                'Релиз - https://jira.mail.ru/projects/BIZ/versions/{version.id}',
                'Упоминаемые в коммитах задачи слинкованы ниже.',
                'Доступен для тестирования по адресам',
                '* http://testbiz.mail.ru (боевая база)',
                '* http://testbiz2.mail.ru (тестовая база)',
            ]),
            'issuetype': {'name': 'Тестирование'},
            'components': [{'name': 'Server'}],
        }
    },
    'deploy': {
        'template': 'MNT-',
        'jql': '',
        'params': {
            'summary': '',
            'description': '\n'.join([
                '',
                '',
            ]),
            'issuetype': {'name': ''},
            'components': [{'name': ''}],
            'fixVersion': None,
        }
    },
    'migration': {
        'template': 'MNT-',
        'jql': '',
        'params': {
            'summary': '',
            'description': '\n'.join([
                '',
                '',
            ]),
            'issuetype': {'name': ''},
            'components': [{'name': ''}],
        }
    },
    'sql': {
        'template': 'MNT-',
        'jql': '',
        'params': {
            'summary': '',
            'description': '\n'.join([
                '',
                '',
            ]),
            'issuetype': {'name': ''},
            'components': [{'name': ''}],
        }
    },
    'config': {
        'template': 'MNT-',
        'jql': '',
        'params': {
            'summary': '',
            'description': '\n'.join([
                '',
                '',
            ]),
            'issuetype': {'name': ''},
            'components': [{'name': ''}],
        }
    },
}

# board https://jira.mail.ru/secure/RapidBoard.jspa?rapidView=1214&useStoredSettings=true
# https://jira.mail.ru/issues/?filter=77902
AGILE_STUFF = {
    'board': {
        'id': 1214,
        'name': 'БИЗ+БИЗОблако {version.name}',
    },
    'filter': {
        'filter_id': 77902,
        'name': 'Релиз БизОблака {version.name}',
        'jql': '\n'.join([
            'project = BIZ',
            'AND issuetype in (Bug, Task, Feature, subtaskIssueTypes())',
            'AND (',
            '    fixVersion in ({version.name})',
            '    OR issuetype in (Bug)',
            '    AND fixVersion is EMPTY',
            '    AND issue in linkedIssuesFromQuery("fixVersion in ({version})", Fixing)',
            '    OR issuetype in (Bug)',
            '    AND fixVersion is EMPTY',
            '    AND issue in linkedIssuesFromQuery("fixVersion in ({version})", Found)',
            '    OR issuetype in (Bug)',
            '    AND fixVersion is EMPTY',
            '    AND affectedVersion = {version.name}',
            ') ORDER BY Rank ASC',
        ]),
    },
    'swimlanes': [
        {
            'name': 'Перенесенные в другой релиз',
            'query': '\n'.join([
                'issuetype in (Bug)',
                'AND fixVersion is EMPTY',
                'AND issue in linkedIssuesFromQuery("fixVersion in (\'{version.name}\')", Found)',
            ]),
            'description': 'Автоматически обновлено {today}',
        },
        {
            'name': 'Баги без таска',
            'query': '\n'.join([
                'issuetype in (Bug)',
                'AND fixVersion is EMPTY',
                'AND issue not in linkedIssuesFromQuery("fixVersion in (\'{version.name}\')", Fixing)'
            ]),
            'description': 'Автоматически обновлено {today}',
        },
        {
            'name': 'Баги',
            'query': '\n'.join([
                'issuetype in (Bug)',
                'AND fixVersion is EMPTY',
                'AND issue in linkedIssuesFromQuery("fixVersion in (\'{version.name}\')", Fixing)'
            ]),
            'description': 'Автоматически обновлено {today}',
        },
        {
            'name': 'Таски облака',
            'query': '\n'.join([
                'fixVersion = "{version.name}"',
                'AND component in (CloudDev)',
            ]),
            'description': 'Автоматически обновлено {today}',
        },
    ],
}

# setup logging
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
handler = logging.StreamHandler()
formatter = logging.Formatter(
    '[%(levelname)s %(asctime)-15s]: %(message)s'
)
handler.setFormatter(formatter)
logger.addHandler(handler)

# constants
BASE_DIR = os.path.dirname(__file__)
config = ConfigParser.ConfigParser()
config.read(os.path.join(BASE_DIR, 'config.conf'))

TASK_RE = re.compile(r'{}-\d+'.format(config.get('jira', 'project_key')))
REVISION_RE = re.compile(r'http://phabricator.corp.mail.ru/D\d+')
VERSION_RE = re.compile(r"__version__ = '([^']+)'")
VERSION_AWARE_RE = re.compile(r'(\d+\.\d+\.\d+)')


### BASIC HELPERS ###
def render_with_context(msg, **kwargs):
    context = {
        'jira': get_jira(),
        'config': config._sections,
        'today': datetime.now().strftime('%d.%m.%Y'),
        'now': datetime.now().strftime('%d.%m.%Y %H:%i:%s'),
    }
    context.update(kwargs)
    # get version as object
    context['version'] = get_jira().get_version(context.get('version'))
    return msg.format(**kwargs)


def confirm(msg, is_interactive=True):
    result = not is_interactive
    if is_interactive:
        answer = raw_input(msg.encode('utf8'))
        result = not answer or 'y' == answer.lower().strip()

    return result


def pdb():
    try:
        import ipdb; ipdb.set_trace()
    except ImportError:
        pass


def get_version_aware(text):
    result = VERSION_AWARE_RE.search(text)
    return result and result.group(1)


### JIRA EXTENSION ###
class XBoard(Board):
    def __init__(self, options, session, raw=None):
        GreenHopperResource.__init__(self, 'rapidviews/{0}', options, session, raw)


class Swimlane(GreenHopperResource):
    def __init__(self, options, session, raw=None):
        GreenHopperResource.__init__(self, 'swimlanes/{0}', options, session, raw)


class JiraAgile(JIRA):
    def update_board_name(self, id, name):
        payload = {
            'id': id,
            'name': name,
        }

        url = self._get_url(
            'rapidviewconfig/name',
            base=self.AGILE_BASE_URL
        )
        response = self._session.put(
            url, data=json.dumps(payload)
        )

        return json_loads(response)

    def board(self, id):
        raw_board_data = self._get_json(
            'xboard/config',
            {'rapidViewId': id},
            base=self.AGILE_BASE_URL
        ).get('currentViewConfig')

        raw_board_data['swimlanes'] = [
            Swimlane(self._options, self._session, raw_swimlane_data)
            for raw_swimlane_data in raw_board_data['swimlanes']
        ]
        result = Board(self._options, self._session, raw_board_data)
        return result

    def update_swimlane(self, board_id, swimlane_id, name=None, query=None, description=None, is_default=None):
        payload = {
            'id': swimlane_id,
        }

        if name:
            payload['name'] = name
        if query:
            payload['query'] = query
        if description:
            payload['description'] = description
        if is_default is not None:
            payload['isDefault'] = is_default

        url = self._get_url(
            'swimlanes/{}/{}'.format(board_id, swimlane_id),
            base=self.AGILE_BASE_URL
        )
        response = self._session.put(
            url, data=json.dumps(payload)
        )

        return json_loads(response)


### JIRA HELPERS ###
def get_jira(cls=None, server=None, credentials=None, options=None, version=None):
    """
    Возвращает инстанс клиента для работы с жирой
    Если такового нет, он, единожды на сессию, создаётся

    Кроме того получает все поля, статусы жиры
    А также версии проекта
    """
    result = get_jira.instance
    if result is None:
        if not credentials:
            credentials = (
                config.get('jira', 'username'),
                config.get('jira', 'password'),
            )

        # Jira Itself
        try:
            address = server or config.get('jira', 'server')
            options = {
                'server': address,
            }
            options.update(options or {})

            if config.getboolean('common', 'debug'):
                httplib.HTTPConnection.debuglevel=2
                httplib.HTTPResponse.debuglevel=2

            cls = cls or JiraAgile
            result = cls(options, basic_auth=credentials)
        except JIRAError:
            logger.error(
                'Twice check your server address "%s" and credentials',
                address
            )
            exit(1)

        result.get_issue_url = lambda x: '{}/browse/{}'.format(
            result.server_info()['baseUrl'],
            x.key,
        )

        # Поля
        field_list = result.fields()
        field_dict = {x['name']:x['id'] for x in field_list}
        result.field_dict = field_dict
        result.get_value = lambda i, k: getattr(i.fields, field_dict[k])

        # Статусы
        result.status_list = result.statuses()
        tmp = defaultdict(list)
        for status in result.status_list:
            tmp[status.raw['statusCategory']['key']].append(status.id)
        result.status_dict = tmp

        # Версия
        result.version_dict = {
            x.name: x
            for x in result.project_versions(
                config.get('jira', 'project_key')
            )
        }
        result.get_version = lambda x=None: result.version_dict.get(
            x or get_version()
        )
        result.get_version_parts = lambda x=None: result.get_version(x).split('.')

        version = version or get_version()
        result.version = result.get_version(version)
        if not result.version:
            logger.error(
                'Jira instance for version "%s" can\'t be initialized',
                version,
            )
            exit(1)

        get_jira.instance = result
        logger.info(
            'Jira instance for version "%s" has been initialized',
            result.version.name,
        )

    return result
get_jira.instance = None


def get_components(component_list_id):
    """
    Возвращает компоненты по id
    """
    if isinstance(component_list_id, basestring):
        component_list_id = [component_list_id]

    result = []
    for key in component_list_id:
        try:
            result.append(
                get_jira().component(key)
            )
        except JIRAError:
            logger.warning(
                'Component %s not found',
                key
            )

    return result


def get_task_list_by_jql(jql, maxResults=50, startAt=0):
    result, page_result = [], None
    while page_result is None or len(page_result) == maxResults:
        try:
            page_result = get_jira().search_issues(
                jql, maxResults=maxResults, startAt=startAt
            )
        except JIRAError as exc:
            logger.error(
                'Failed to search Jira isses with JQL "%s": %s',
                jql, exc
            )
            page_result = []

        result += page_result
        startAt += maxResults

    return result


def get_task_by_jql(jql):
    result = get_task_list_by_jql(jql)
    result = result and result[0] or None
    return result


def get_task_by_key(key):
    try:
        result = get_jira().issue(key)
    except JIRAError as exc:
        logger.error(
            'Failed to get issue "%s": %s',
            key, exc
        )
        result = None

    return result


def get_issue(issue):
    """
    Возвращает таски по ключу
    """
    if isinstance(issue, basestring):
        issue = get_task_by_key(issue)

    issue.simplifiedissuelinks = [
        (
            getattr(x, 'inwardIssue', None)
            or getattr(x, 'outwardIssue', None)
        ).key
        for x in issue.fields.issuelinks
    ]

    return issue


def get_issues(issue_list):
    """
    Возвращает таски по ключам
    """
    if isinstance(issue_list, Issue):
        issue_list = [issue_list]
    elif isinstance(issue_list, basestring):
        result = [get_issue(issue_list)]
    elif isinstance(issue_list[0], basestring):
        jql = 'project={} AND issue IN ({})'.format(
            config.get('jira', 'project_key'),
            ', '.join(["'{}'".format(x) for x in issue_list])
        )

        issue_list = get_task_list_by_jql(jql)

    return issue_list


### GIT HELPERS ###
def get_repo(path=None):
    result = get_repo.instance
    if result is None:
        get_repo.instance = result = Repo(
            path or config.get('repo', 'path')
        )

    return result
get_repo.instance = None


def get_version(branch=None):
    """
    Возвращает версию сервиса в rpm или текущей ветке
    """
    repo = get_repo()
    content = repo.git.show('{}:pdd/__init__.py'.format(branch or config.get('repo', 'branch')))
    result = VERSION_RE.search(content)
    return result and result.group(1)


def get_commits(branch=None, max_count=100):
    """
    Возвращает нужное колличество коммитов
    из определенной ветки репозитория
    """
    repo = get_repo()
    return repo.iter_commits(
        branch or config.get('repo', 'branch'),
        max_count=max_count
    )


def get_prepare_release_task():
    version = get_version()
    jira_params = TASK_TEMPLATE_DICT.get('prepare_release')
    jira_params = jira_params['summary'].format(version)
    jql = (
        'project = {project} '
        'AND summary ~ "{summary}"'
    ).format(
        project=config.get('jira', 'project_key'),
        summary=summary,
    )

    return get_task_by_jql(jql)


def get_test_release_task():
    version = get_version()

    jira_params = TASK_TEMPLATE_DICT.get('test_release')
    jira_params['summary'].format(
        version=version,
    )
    jql = (
        'project = {project} '
        'AND summary ~ "{summary}"'
    ).format(
        project=config.get('jira', 'project_key'),
        summary=summary,
    )

    return get_task_by_jql(jql)


def get_release_task(version=None):
    version = version or get_version()

    jira_params = prepare_task_params('release_task')
    jira_params['summary'].format(
        version=version,
    )
    jql = render_with_context(
        'project = {config.jira.project_key} '
        'AND summary ~ "{task[summary]}"',
        project='MNT',
        summary=jira_params.get('summary'),
        version=version,
    )

    return get_task_by_jql(jql)


def get_weekly_dev_task(version=None):
    """
    Возвращает таск для еженедельной разработки
    * Либо в этой версии уже есть такой таск
    * Либо взять последний и заинкрементить номер
    """
    version = version or get_version()

    template = TASK_TEMPLATE_DICT.get('weekly_dev')
    summary = template['params']['summary'].format(number='').strip()
    jql = render_with_context(
        'project = {project_key} '
        'AND summary ~ "{summary}" '
        'ORDER BY created DESC',
        project_key='BIZ',
        summary=summary,
    )

    task = get_task_by_jql(jql)
    number = int(task.fields.summary[-1]) + 1 if task else 1
    return create_task('weekly_dev', additional_context={'number': number})


def transit_tasks(issue_key_list, transition_key):
    """
    Позволяет перевести несколько тасков
    в какое-то состояние и не умереть
    """
    if isinstance(issue_key_list, basestring):
        issue_key_list = [issue_key_list]

    for issue_key in issue_key_list:
        transition_list = get_jira().transitions(issue_key)
        for transition in transition_list:
            need_transition = (
                transition['to']['name'] == transition_key
                or transition['to']['id'] == transition_key
            )
            if need_transition:
                logger.info(
                    'Transit issue %s to %s has been successfully done',
                    issue_key, transition_key
                )

                try:
                    get_jira().transition_issue(
                        issue_key,
                        transition_key
                    )
                except JIRAError as exc:
                    logger.exception(
                        'Transit issue %s to %s has failed: %s',
                        issue_key, transition_key, exc
                    )

                break

    return True


DEFAULT_FIELDS = (
    'summary', 'description', 'project',
    'components', 'issuetype', 'fixVersion',
)


def prepare_task_params(template_name=None, params=None, additional_context=None, version=None, is_interactive=True):
    version = get_jira().get_version(version)
    if not params:
        params = {
            'project': {'key': config.get('jira', 'project_key')},
            'summary': 'New issue from jira-python',
            'description': 'Look into this one',
            'issuetype': {'name': 'Feature'},
            'components': {'name': 'Server'},
            'Fix Version/s': [{'id': version.id}],
        }

    template = template_name and TASK_TEMPLATE_DICT.get(template_name)
    if not template:
        logger.error(
            'Failed to create task: unknown template name "%s"',
            template_name
        )
        return None

    params.update(template.get('params') or {})

    jira_params = {}
    for key, value in params.items():
        jira_key = (
            key in DEFAULT_FIELDS and key
            or get_jira().field_dict.get(key)
        )
        if not jira_key:
            logger.warning(
                'Skip param "%s" for new task "%s": unknown key',
                key, template
            )
            continue

        jira_value = value
        if isinstance(jira_value, basestring):
            context = {
                'version': version,
            }
            context.update(additional_context or {})
            jira_value = jira_value.format(**context)
        # elif key == 'components':
        #     params[key] = get_components(value)

        jira_params[jira_key] = jira_value

    return jira_params


def get_task_by_params(template_name, params=None, additional_context=None, version=None, jql=None):
    template = template_name and TASK_TEMPLATE_DICT.get(template_name)
    if not template:
        logger.error(
            'Failed to create task: unknown template name "%s"',
            template_name
        )
        return None

    jira_params = prepare_task_params(template_name, params, additional_context, version)
    jql = render_with_context(
        template['jql'],
        task=jira_params,
    )

    return get_task_by_jql(jql)


class MyEncoder(json.JSONEncoder):
    def default(self, o):
        return str(o)


def dumps(data):
    return json.dumps(
        data, indent=2,
        ensure_ascii=False, cls=MyEncoder
    )


def create_task(template_name=None, params=None, additional_context=None, version=None, is_interactive=True):
    jira_params = prepare_task_params(template_name, params, additional_context, version)
    jql = render_with_context(
        'summary ~ "{task[summary]}"',
        task=jira_params,
    )
    created, issue = False, get_task_by_jql(jql)

    if issue:
        logger.info(
            'Task "%s" has been found: %s',
            issue.fields.summary,
            get_jira().get_issue_url(issue)
        )
    else:
        msg = '{}\n\nAre you sure you want to create the task? [Y/n]\n'.format(
            dumps(jira_params),
        )
        if confirm(msg, is_interactive):
            logger.info(
                'Task "%s" creation...',
                jira_params['summary']
            )
            try:
                issue = get_jira().create_issue(fields=jira_params)
                created = True

                url = get_jira().get_issue_url(issue)
                webbrowser.open(url, new=2)
                logger.info(
                    'Task "%s" has been created: %s',
                    jira_params['summary'], url
                )
            except JIRAError as exc:
                logger.exception(
                    'Task "%s" creation has been failed: %s',
                    jira_params['summary'], exc
                )
        else:
            logger.info(
                'Task "%s" creation has been skipped by user',
                jira_params['summary'],
            )

    return created, issue


def get_tasks(version=None):
    """
    Вовзращает таски,
    которые предположительно составляют релиз
    Поскольку работа всегда идёт с rpm-веткой
    Алгоритм достаточно прочный,
    если rpm-ветка соответствует ветке релиза
    """
    version = version and get_jira().get_version(version) or get_jira().version
    version = version.name.split('.')
    orphan_commit_list = []
    task_commit_list = set()
    task_list = set()
    for commit in get_commits():
        logger.debug(
            'Commit "%s" by %s at %s',
            commit, commit.author, commit.committed_datetime
        )

        # Наличие коммита с мажорной версией означает
        # что всё что дальше, уже в продакшене
        # при вводе минорных версий это может быть не так
        # когда, например, минорный релиз собирается из выборочных тасков
        commit_version = get_version_aware(commit.message)
        commit_version = commit_version and commit_version.split('.')
        if 'Version' in commit.message and commit_version[1] < version[1]:
            logger.info(
                'Found version commit %s: break',
                commit.message
            )
            break

        # Мы ищем имена тасков по нашему проекту
        tmp = TASK_RE.findall(commit.message)
        if tmp:
            task_list.update(tmp)
            task_commit_list.add(commit)
        else:
            # без тасков, это не нормально и их надо глянуть руками
            logger.warning(
                'Commit "%s" without task',
                commit,
            )
            orphan_commit_list.append(commit)

    logger.info(
        'Found tasks: %s',
        ' '.join(task_list) or 'N/A'
    )
    logger.debug(
        'Found task commits: %s',
        ' '.join(['{}'.format(x) for x in task_commit_list])
        or 'N/A'
    )
    for commit in orphan_commit_list:
        revision = REVISION_RE.search(commit.message)
        revision = revision.group() if revision else 'N/A'
        logger.info(
            'Found orphan commit "%s": %s - %s',
            commit, revision, commit.message
        )

    result = []
    ready_list = []
    for task in get_issues(tuple(task_list)):
        if is_issue_done(task):
            ready_list.append(task)
        else:
            result.append(task)

    logger.info(
        'New tasks for version %s: %s',
        get_version(),
        ' '.join([x.key for x in result]) or 'N/A'
    )
    logger.info(
        'Closed tasks: %s',
        ' '.join([
            '{} ({})'.format(
                x.key,
                ', '.join(x.name for x in x.fields.fixVersions)
            )
            for x in ready_list
        ]) or 'N/A'
    )

    return result


def inspect_task(issue):
    issue = get_issue(issue)
    data = [(k, v) for k, v in [
        (x, getattr(issue.fields, x))
        for x in dir(issue.fields)
        if x.startswith('customfield_')
    ] if v]

    print 'Custom fields'
    for k, v in data:
        print('{} ({}): {}'.format(
            get_jira().field_dict[k],
            k, v
        ))

    for link in issue.fields.issuelinks:
        outwardIssue = getattr(link, 'outwardIssue', None)
        if outwardIssue:
            outwardIssue = link.outwardIssue
            print("\tOutward: " + outwardIssue.key)

        inwardIssue = getattr(link, 'inwardIssue', None)
        if inwardIssue:
            inwardIssue = link.inwardIssue
            print("\tInward: " + inwardIssue.key)

    # fixVersions - Fix Version/s
    print issue.fields.fixVersions
    # testplan
    print issue.fields.customfield_10700
    # versions - Affects Version/s
    print issue.fields.versions
    # version by id - jira.version('68537')

    import ipdb; ipdb.set_trace()


def link_issues(parent_issue, issue_list, relation_type='relates to', is_interactive=True):
    parent_issue = get_issue(parent_issue)
    issue_list = get_issues(issue_list)

    logger.info(
        'Linking tasks to %s (%s) as "%s"...',
        parent_issue.key,
        parent_issue.fields.summary,
        relation_type
    )

    for issue in issue_list:
        if issue.key in parent_issue.simplifiedissuelinks:
            logger.info(
                'Linking of %s has been skipped: already linked',
                issue.key,
            )
            continue

        msg = 'Are you sure you want to link {} ({})? [Y/n]\n'.format(
            issue.key, issue.fields.summary
        )
        if not confirm(msg, is_interactive):
            logger.info(
                'Linking of %s has been skipped: not confirmed',
                issue.key
            )
            continue

        try:
            get_jira().create_issue_link(
                type=relation_type,
                inwardIssue=parent_issue.key,
                outwardIssue=issue.key,
            )
            logger.info(
                'Issue %s "%s" %s',
                issue, relation_type, parent_issue,
            )
        except JIRAError as exc:
            logger.error(
                'Issue %s was not linked to %s with relation type %s: %s',
                issue, parent_issue, relation_type, exc
            )

    return parent_issue


def is_issue_done(issue, version=None):
    """
    Нужно узнать велись ли по таску работы
    * Если есть коммент с клозет ревизией
    * Если таска закрыта, протестирована, хз
    * Если версия не текущая или не проставлена
    """
    version = version or get_version()
    issue = get_issue(issue)
    result = False

    version_list = [x.name for x in issue.fields.fixVersions]
    is_not_current_version_task = (
        (not version or version not in version_list)
        and (
            issue.fields.status.id in get_jira().status_dict.get('done')
            or issue.fields.status.id in get_jira().status_dict.get('new')
            or issue.fields.status.name in ('Awaiting', )
        )
    )

    if is_not_current_version_task:
        if [1 for x in version_list if x > version]:
            logger.error(
                'Oops it seems like there is task from newer version: %s',
                ', '.join(version_list)
            )
        else:
            logger.debug(
                'Issue %s has been closed in other version',
                issue.key,
            )

            result = True

    # comment_list = (
    #     issue.fields.status.id not in get_jira().status_dict.get('done')
    #     and get_jira().comments(issue.key)[::-1]
    #     or ()
    # )
    # for comment in comment_list:
    #     result = (
    #         'phabricator.corp.mail.ru' in comment.body
    #         and 'closed D' in comment.body
    #     )
    #     break

    return result


def review_issue(issue_key):
    logger.info('Issue "%s" is reviewing...', issue_key)
    # Получаем таск

    # Если есть версия или он закрыт - всё ок

    # Если это баг, то смотрим воспроизведение
    # Если нет, то наличие тестплана
    # Если тестплана нет, то комментим в духе "Где тестплан?"
    # Если нет описания, то комментим в духе "Где описание?"

    # Меняем состояние на deployed
    # Проставляем версию релиза


    logger.info('Issue "%s" has been reviewed', issue_key)
    return True


def prepare_release(*args, **kwargs):
    version = get_jira().version.name
    logger.info('Release "%s" is preparing', version)

    # Создать таск на релиз на себя или взять готовый
    prepare_task_created, prepare_task = create_task('prepare_release')

    # Создать таск на тестирование или взять готовый
    test_task_created, test_task = create_task('test_release')

    # Связать два таска, если связи нет
    link_issues(prepare_task, test_task, relation_type='is triggering', is_interactive=False)

    # Заревьюить все таски
    release_task_list = get_tasks()

    # Прилинковать к таску на тестирование все таски
    # Если таск не заревьюился автоматом, показать его
    link_issues(test_task, release_task_list)

    # Если таск на тестирование создавался,
    # то перевести на исполнителя
    if test_task_created:
        logger.info(
            'Assign test task "%s" to tester',
            test_task.key
        )

    # Апгрейднуть борды
    logger.info(
        'Agile updation...'
    )
    update_filter(AGILE_STUFF['filter'], version=version)
    get_jira().update_board_name(AGILE_STUFF['board']['name'])
    update_swimlanes(AGILE_STUFF['swimlanes'], version=version)

    # Если таск на тестирование в начальном статусе,
    # Оповестить всех, что тестирование началось
    logger.info(
        'Deploy task creation checking...'
    )
    if test_task.fields.status.name in ('tested', ):
        # Если в конечном статусе,
        # Создать таск на раскладку
        deploy_task_created, deploy_task = create_task(
            'deploy', version=version
        )
    else:
        logger.info(
            'Deploy task creation has been skipped: %s',
            test_task.key, test_task.fields.status.name
        )

    logger.info('Release "%s" has been prepared', version)
    return True


def update_filter(filter_params, version=None):
    version = get_jira().version

    jira_params = {
        'description': 'Автоматическо обновлено {}'.format(
            datetime.now().strftime('%d.%m.%Y %H:%i:%s')
        )
    }
    for key, value in filter_params.items():
        jira_params[key] = unicode(value).format(version=version)

    result = False

    try:
        url = 'https://jira.mail.ru/issues/?filter={filter_id}'.format(
            **jira_params
        )
        get_jira().update_filter(**jira_params)
        logger.info(
            'Filter %s (%s) has been successfuly updated',
            jira_params['filter_id'], jira_params['description']
        )
        webbrowser.open(url, new=2)
        result = True
    except JIRAError as exc:
        logger.error(
            'Filter %s (%s) has failed to update: %s',
            jira_params['filter_id'], jira_params['filter_id'], exc
        )

    return result


def update_swimlanes(swimlane_params_list, version=None):
    """
    https://jira.mail.ru/rest/agile/1.0/application.wadl
    https://jira.mail.ru/rest/greenhopper/1.0/application.wadl

    https://jira.mail.ru/secure/RapidView.jspa?rapidView=1214&tab=filter
    curl 'https://jira.mail.ru/rest/greenhopper/1.0/rapidviewconfig/name' -X PUT
    --data-binary $'{"id":1214,"name":"\u0411\u0418\u0417+\u0411\u0418\u0417\u041e\u0431\u043b\u0430\u043a\u043e 1.89.0"}'

    https://jira.mail.ru/issues/?filter=77902

    https://jira.mail.ru/secure/RapidView.jspa?rapidView=1214&tab=swimlanes
    swimlanes 2549 2551 2553 2554
    curl 'https://jira.mail.ru/rest/greenhopper/1.0/swimlanes/1214/2549' -X PUT
    --data-binary '{"id":2549,"query":"fixVersion = \"1.89.0\" AND component in (CloudDev)"}'
    """
    version = version or get_version()

    for swimlane_params in swimlane_params_list:
        jira_params = {}
        for key, value in swimlane_params.items():
            jira_params[key] = render_with_context(
                value,
                version=version
            )

            try:
                get_jira().update_swimlane(**jira_params)
            except JIRAError as exc:
                logger.exception(
                    'Swimlane %s updating has been failed: %s',
                    jira_params['id'], exc
                )

    return True


def close_release(*args, **kwargs):
    """
    * Выбираем последний релиз
    * Смотрим, что разложилось
    * Берем линкованные таски
    * Переводим в релисд
    """
    version = get_version()

    logger.info('Release "%s" is closing', version)

    params = TASK_TEMPLATE_DICT.get('release_task') or {}
    summary = params.get('summary', '').format(version=version)

    jql = render_with_context(
        'project = {project}'
        'AND summary ~ {task[summary]}',
        project=config.get('jira', 'project_key'),
        task=params,
    )
    task_list = get_jira().get_task_by_jql(jql)
    import ipdb; ipdb.set_trace()
    for task in task_list:
        logger.info('Found task "%s"', task)
        transition_issue(task, 'released')

    logger.info('Release "%s" has been closed', version)


def console(*args, **kwargs):
    import ipdb; ipdb.set_trace()


def phab(*args, **kwargs):
    """
    {
        u'userName': u's.trofimov',
        u'primaryEmail': u's.trofimov@corp.mail.ru',
        u'phid': u'PHID-USER-3ipdpoxtzw4wdlkea4w7',
        u'realName': u'Sergey Trofimov',
        u'roles': [u'admin', u'verified', u'approved', u'activated'],
        u'image': u'http://phabricator.corp.mail.ru/file/data/mrwzwutqaolpy5zqji6e/PHID-FILE-pemgj2dhdbwmlffi3j7s/profile',
        u'uri': u'http://phabricator.corp.mail.ru/p/s.trofimov/'
    }
    """
    from phabricator import Phabricator
    phab = Phabricator()  # This will use your ~/.arcrc file
    me = phab.user.whoami()
    import ipdb; ipdb.set_trace()


if __name__ == '__main__':
    logger.info('Starting')

    # simple argparse
    args, kwargs = [], {}
    for item in sys.argv[2:]:
        if item.startswith('--'):
            key, value = item[2:].split('=')
            kwargs[key] = value
        elif item.startswith('-'):
            args.append(item[1:])

    hlp = 'args: {}; kwargs: {}'.format(
        ', '.join([x for x in args]) or 'n/a',
        ', '.join(['{}={}'.format(k, v) for k, v in kwargs.items()]) or 'n/a',
    )

    # simple action calling
    cmd_name = sys.argv[1] if len(sys.argv) > 1 else 'prepare_release'
    cmd = locals().get(cmd_name)
    if callable(cmd):
        get_jira(version=kwargs.get('version'))
        logger.info(
            'Calling command "%s" with %s',
            cmd_name, hlp
        )
        cmd(*args, **kwargs)
    else:
        logger.warning(
            'Bad command "%s" with %s',
            cmd_name, hlp
        )

    logger.info('Completed')
