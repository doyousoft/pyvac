# -*- coding: utf-8 -*-
import re
import json
import logging
from datetime import datetime

from .base import View

from pyramid.httpexceptions import HTTPFound
from pyramid.url import route_url
from pyramid.settings import asbool

from pyvac.models import Request, VacationType, User
# from pyvac.helpers.i18n import trans as _
from pyvac.helpers.calendar import delFromCal
from pyvac.helpers.ldap import LdapCache
from pyvac.helpers.holiday import get_holiday
from pyvac.helpers.util import daterange, JsonHTTPNotFound

import yaml
try:
    from yaml import CSafeLoader as YAMLLoader
except ImportError:
    from yaml import SafeLoader as YAMLLoader

log = logging.getLogger(__name__)


class Send(View):

    def render(self):
        try:
            form_date_from = self.request.params.get('date_from')
            if ' - ' not in form_date_from:
                msg = 'Invalid format for period.'
                self.request.session.flash('error;%s' % msg)
                return HTTPFound(location=route_url('home', self.request))

            dates = self.request.params.get('date_from').split(' - ')
            date_from = datetime.strptime(dates[0], '%d/%m/%Y')
            date_to = datetime.strptime(dates[1], '%d/%m/%Y')

            # retrieve holidays for user so we can remove them from selection
            holidays = get_holiday(self.user, year=date_from.year,
                                   use_datetime=True)

            submitted = [d for d in daterange(date_from, date_to)
                         if d.isoweekday() not in [6, 7]
                         and d not in holidays]
            days = float(len(submitted))
            pool = None

            days_diff = (date_to - date_from).days
            if days_diff < 0:
                msg = 'Invalid format for period.'
                self.request.session.flash('error;%s' % msg)
                return HTTPFound(location=route_url('home', self.request))

            if (date_to == date_from) and days > 1:
                # same day, asking only for one or less day duration
                msg = 'Invalid value for days.'
                self.request.session.flash('error;%s' % msg)
                return HTTPFound(location=route_url('home', self.request))

            if days <= 0:
                msg = 'Invalid value for days.'
                self.request.session.flash('error;%s' % msg)
                return HTTPFound(location=route_url('home', self.request))

            # retrieve future requests for user so we can check overlap
            futures = [d for req in
                       Request.by_user_future(self.session, self.user)
                       for d in daterange(req.date_from, req.date_to)]

            intersect = set(futures) & set(submitted)
            if intersect:
                msg = 'Invalid period: days already requested.'
                self.request.session.flash('error;%s' % msg)
                return HTTPFound(location=route_url('home', self.request))

            vac_type = VacationType.by_id(self.session,
                                          int(self.request.params.get('type')))

            # check if vacation requires user role
            if (vac_type.visibility
                    and self.user.role not in vac_type.visibility):
                msg = 'You are not allowed to use type: %s' % vac_type.name
                self.request.session.flash('error;%s' % msg)
                return HTTPFound(location=route_url('home', self.request))

            # label field is used when requesting half day
            label = u''
            breakdown = self.request.params.get('breakdown')
            if breakdown != 'FULL':
                # handle half day
                if (days > 1):
                    msg = ('AM/PM option must be used only when requesting a '
                           'single day.')
                    self.request.session.flash('error;%s' % msg)
                    return HTTPFound(location=route_url('home', self.request))
                else:
                    days = 0.5
                    label = unicode(breakdown)

            # check RTT usage
            if vac_type.name == u'RTT':
                pool = rtt_data = self.user.get_rtt_usage(self.session)
                if rtt_data is not None and rtt_data['left'] <= 0:
                    msg = 'No RTT left to take.'
                    self.request.session.flash('error;%s' % msg)
                    return HTTPFound(location=route_url('home', self.request))
                # check that we have enough RTT to take
                if rtt_data is not None and days > rtt_data['left']:
                    msg = 'You only have %s RTT to use.' % rtt_data['left']
                    self.request.session.flash('error;%s' % msg)
                    return HTTPFound(location=route_url('home', self.request))
                # check that we request vacations in the allowed year
                if rtt_data is not None and (
                        date_from.year != rtt_data['year'] or
                        date_to.year != rtt_data['year']):
                    msg = ('RTT can only be used for year %d.' %
                           rtt_data['year'])
                    self.request.session.flash('error;%s' % msg)
                    return HTTPFound(location=route_url('home', self.request))

            message = None
            # check Exceptionnel mandatory field
            if vac_type.name == u'Exceptionnel':
                message = self.request.params.get('exception_text')
                message = message.strip() if message else message
                if not message:
                    msg = ('You must provide a reason for %s requests' %
                           vac_type.name)
                    self.request.session.flash('error;%s' % msg)
                    return HTTPFound(location=route_url('home', self.request))
                # check size
                if len(message) > 140:
                    msg = ('%s reason must not exceed 140 characters' %
                           vac_type.name)
                    self.request.session.flash('error;%s' % msg)
                    return HTTPFound(location=route_url('home', self.request))

            # check Récupération reason field
            if vac_type.name == u'Récupération':
                message = self.request.params.get('exception_text')
                message = message.strip() if message else message
                # check size
                if message and len(message) > 140:
                    msg = ('%s reason must not exceed 140 characters' %
                           vac_type.name)
                    self.request.session.flash('error;%s' % msg)
                    return HTTPFound(location=route_url('home', self.request))

            # create the request
            # default values
            target_status = u'PENDING'
            target_user = self.user
            target_notified = False

            sudo_use = False
            if self.user.is_admin:
                sudo_user_id = int(self.request.params.get('sudo_user'))
                if sudo_user_id != -1:
                    user = User.by_id(self.session, sudo_user_id)
                    if user:
                        sudo_use = True
                        target_user = user
                        target_status = u'APPROVED_ADMIN'
                        target_notified = True

            # save pool status when making the request
            if pool:
                pool_status = json.dumps(pool)
            else:
                pool_status = json.dumps({})

            request = Request(date_from=date_from,
                              date_to=date_to,
                              days=days,
                              vacation_type=vac_type,
                              status=target_status,
                              user=target_user,
                              notified=target_notified,
                              label=label,
                              message=message,
                              pool_status=pool_status,
                              )
            self.session.add(request)
            self.session.flush()

            if request and not sudo_use:
                msg = 'Request sent to your manager.'
                self.request.session.flash('info;%s' % msg)
                # call celery task directly, do not wait for polling
                from celery.registry import tasks
                from celery.task import subtask
                req_task = tasks['worker_pending']
                data = {'req_id': request.id}
                subtask(req_task).apply_async(kwargs={'data': data},
                                              countdown=5)
                log.info('scheduling task worker_pending for %s' % data)

            if request and sudo_use:
                settings = self.request.registry.settings
                if 'pyvac.celery.yaml' in settings:
                    with open(settings['pyvac.celery.yaml']) as fdesc:
                        Conf = yaml.load(fdesc, YAMLLoader)
                    caldav_url = Conf.get('caldav').get('url')
                    request.add_to_cal(caldav_url)
                    msg = 'Request added to calendar and DB.'
                    self.request.session.flash('info;%s' % msg)

        except Exception as exc:
            log.error(exc)
            msg = ('An error has occured while processing this request: %r'
                   % exc)
            self.request.session.flash('error;%s' % msg)

        return HTTPFound(location=route_url('home', self.request))


class List(View):
    """
    List all user requests
    """

    def get_conflict(self, requests):
        """ Returns requests conflicts """
        conflicts = {}
        for req in requests:
            req.conflict = [req2.summary for req2 in
                            Request.in_conflict_ou(self.session, req)]
            if req.conflict:
                req.conflict = {'': req.conflict}
                if req.id not in conflicts:
                    conflicts[req.id] = {}
                conflicts[req.id][''] = '\n'.join(req.conflict[''])

        return conflicts

    def get_conflict_by_teams(self, requests, users_teams):
        """ Returns requests conflicts by teams """
        conflicts = {}
        for req in requests:
            user_teams = users_teams.get(req.user.dn, [])
            matched = {}
            # for all requests in conflict with current req
            for req2 in Request.in_conflict(self.session, req):
                # if we have some match between request teams
                # and conflicting request teams
                conflict_teams = users_teams.get(req2.user.dn, [])
                common_set = set(conflict_teams) & set(user_teams)
                if common_set:
                    for team in common_set:
                        if team not in matched:
                            matched[team] = []
                        matched[team].append(req2.summary)

            req.conflict = matched
            if req.conflict:
                for team in req.conflict:
                    if req.id not in conflicts:
                        conflicts[req.id] = {}
                    conflicts[req.id][team] = ('\n'.join([team] +
                                               req.conflict[team]))

        return conflicts

    def render(self):

        req_list = {'requests': [], 'conflicts': {}}
        requests = []
        if self.user.is_admin:
            country = self.user.country
            requests = Request.all_for_admin_per_country(self.session,
                                                         country)
            # check if admin user is also a manager, in this case merge all
            # requests
            requests_manager = Request.by_manager(self.session, self.user)
            # avoid duplicate entries
            req_to_add = [req for req in requests_manager
                          if req not in requests]
            requests.extend(req_to_add)
        elif self.user.is_super:
            requests = Request.by_manager(self.session, self.user)

        req_list['requests'] = requests

        # always add our requests
        for req in Request.by_user(self.session, self.user):
            if req not in req_list['requests']:
                req_list['requests'].append(req)

        # split requests by past/next
        today = datetime.now()
        past_req = [req for req in req_list['requests']
                    if req.date_to < today
                    and req.status != 'PENDING']

        next_req = [req for req in req_list['requests']
                    if req not in past_req]

        req_list['past'] = past_req
        req_list['next'] = next_req

        # only retrieve conflicts for super users
        # only retrieve conflicts for next requests, not past ones
        if req_list['next'] and self.user.is_super:
            conflicts = {}

            settings = self.request.registry.settings
            use_ldap = False
            if 'pyvac.use_ldap' in settings:
                use_ldap = asbool(settings.get('pyvac.use_ldap'))

            if use_ldap:
                ldap = LdapCache()
                users_teams = {}
                for team, members in ldap.list_teams().iteritems():
                    for member in members:
                        users_teams.setdefault(member, []).append(team)

                conflicts = self.get_conflict_by_teams(req_list['next'],
                                                       users_teams)
            else:
                conflicts = self.get_conflict(req_list['next'])

            req_list['conflicts'] = conflicts

        return req_list


class Accept(View):
    """
    Accept a request
    """

    def render(self):

        req_id = self.request.params.get('request_id')
        req = Request.by_id(self.session, req_id)
        if not req:
            return ''

        data = {'req_id': req.id}

        only_manager = False
        # we should handle the case where the admin is also a user manager
        if (self.user.ldap_user and (req.user.manager_dn == self.user.dn)
                and (req.status == 'PENDING')):
            only_manager = True

        if self.user.is_admin and not only_manager:
            req.update_status('APPROVED_ADMIN')
            # save who performed this action
            req.last_action_user_id = self.user.id

            task_name = 'worker_approved'
            settings = self.request.registry.settings
            with open(settings['pyvac.celery.yaml']) as fdesc:
                Conf = yaml.load(fdesc, YAMLLoader)
            data['caldav.url'] = Conf.get('caldav').get('url')
        else:
            req.update_status('ACCEPTED_MANAGER')
            # save who performed this action
            req.last_action_user_id = self.user.id
            task_name = 'worker_accepted'

        self.session.flush()

        # call celery task directly, do not wait for polling
        from celery.registry import tasks
        from celery.task import subtask
        req_task = tasks[task_name]

        subtask(req_task).apply_async(kwargs={'data': data}, countdown=5)

        log.info('scheduling task %s for %s' % (task_name, data))
        return req.status


class Refuse(View):
    """
    Refuse a request
    """

    def render(self):

        req_id = self.request.params.get('request_id')
        req = Request.by_id(self.session, req_id)
        if not req:
            return ''
        reason = self.request.params.get('reason')

        req.reason = reason
        req.update_status('DENIED')
        # save who performed this action
        req.last_action_user_id = self.user.id
        self.session.flush()

        # call celery task directly, do not wait for polling
        from celery.registry import tasks
        from celery.task import subtask
        req_task = tasks['worker_denied']
        data = {'req_id': req.id}
        subtask(req_task).apply_async(kwargs={'data': data}, countdown=5)

        log.info('scheduling task worker_denied for %s' % data)

        return req.status


class Cancel(View):
    """
    Cancel a request and remove entry from calendar if needed.
    """

    def render(self):

        req_id = self.request.params.get('request_id')
        req = Request.by_id(self.session, req_id)
        if not req:
            return ''

        # check if request have already been consumed
        if not self.user.is_admin:
            today = datetime.now()
            if req.date_from <= today:
                log.error('User %s tried to CANCEL consumed request %d.' %
                          (self.user.login, req.id))
                return req.status

        # delete from calendar
        if req.status == 'APPROVED_ADMIN' and req.ics_url:
            settings = self.request.registry.settings
            with open(settings['pyvac.celery.yaml']) as fdesc:
                Conf = yaml.load(fdesc, YAMLLoader)
            caldav_url = Conf.get('caldav').get('url')
            delFromCal(caldav_url, req.ics_url)

        req.update_status('CANCELED')
        # save who performed this action
        req.last_action_user_id = self.user.id

        self.session.flush()
        return req.status


class Export(View):
    """
    Display form to export requests
    """
    def render(self):
        from datetime import datetime
        start = datetime(2014, 5, 1)
        today = datetime.now()
        entries = []
        for year in reversed(range(start.year, today.year + 1)):
            for month in reversed(range(1, 13)):
                temp = datetime(year, month, 1)
                entries.append(('%d/%d' % (month, year),
                               temp.strftime('%B %Y')))

        return {'months': entries,
                'current_month': '%d/%d' % (today.month, today.year)}


class Exported(View):
    """
    Export all requests of a month to csv
    """
    def render(self):

        exported = {}
        if self.user.is_admin:
            country = self.user.country
            month, year = self.request.params.get('month').split('/')
            requests = Request.get_by_month(self.session, country,
                                            int(month), int(year))
            data = []
            header = ('%s,%s,%s,%s,%s,%s,%s,%s,%s' %
                      ('#', 'lastname', 'firstname', 'from', 'to', 'number',
                       'type', 'label', 'message'))
            data.append(header)
            for idx, req in enumerate(requests, start=1):
                data.append('%d,%s' % (idx, req.summarycsv))
            exported = '\n'.join(data)
        return {u'exported': exported}


class Prevision(View):
    """
    Display future CP used per user
    """
    def render(self):
        from datetime import datetime
        today = datetime.now()
        end_date = datetime(today.year, 10, 31)

        previsions = Request.get_previsions(self.session, end_date)

        users_per_id = dict([(user.id, user)
                             for user in User.find(self.session)])

        settings = self.request.registry.settings
        use_ldap = False
        if 'pyvac.use_ldap' in settings:
            use_ldap = asbool(settings.get('pyvac.use_ldap'))

        user_attr = {}
        users_teams = {}
        if use_ldap:
            # synchronise user groups/roles
            User.sync_ldap_info(self.session)
            ldap = LdapCache()
            user_attr = ldap.get_users_units()
            users_teams = {}
            for team, members in ldap.list_teams().iteritems():
                for member in members:
                    users_teams.setdefault(member, []).append(team)

        return {'users_per_id': users_per_id,
                'use_ldap': use_ldap,
                'ldap_info': user_attr,
                'users_teams': users_teams,
                'previsions': previsions,
                'today': today,
                'end_date': end_date,
                }


class Off(View):
    """
    Return all users who are on vacation for given date

    If no date provided, default to current date
    Results can be filtered in querystring by user name or nickname (LDAP uid)
    """
    def update_response(self, response):
        """ Do nothing """

    def render(self):
        def fmt_req_type(req):
            label = ' %s' % req.label if req.label else ''
            return '%s%s' % (req.type, label)

        filter_nick = self.request.params.get('nick')
        filter_name = self.request.params.get('name')
        filter_date = self.request.params.get('date')
        # strict if provided will disable partial search for nicknames
        strict = self.request.params.get('strict')

        # remove unwanted chars from filter_date
        if filter_date:
            filter_date = re.sub('[^\d+]', '', filter_date)

        if filter_nick:
            # retrieve all availables nicknames
            all_nick = [nick.lower()
                        for nick in User.get_all_nicknames(self.session)]
            if strict:
                match = filter_nick.lower() in all_nick
            else:
                match = set([nick for nick in all_nick
                             if filter_nick.lower() in nick.lower()])
            if not match:
                # filter_nick does not match any known uid, stop here
                return JsonHTTPNotFound({'message': ('%s not found'
                                                     % filter_nick)})

        requests = Request.get_active(self.session, filter_date)
        data_name = dict([(req.user.name.lower(), fmt_req_type(req))
                          for req in requests])
        data_nick = dict([(req.user.nickname, fmt_req_type(req))
                          for req in requests])

        ret = val = None
        if filter_nick:
            val = data_nick.get(filter_nick.lower())
            if val:
                ret = {filter_nick: val}
            elif not strict:
                val = dict([(k, v) for k, v in data_nick.items()
                            if filter_nick.lower() in k])
                return val
            else:
                return {}

        if filter_name:
            val = data_name.get(filter_name.lower())
            if val:
                ret = {filter_name: val}
            else:
                val = dict([(k, v) for k, v in data_name.items()
                            if filter_name.lower() in k])
                return val

        return ret if ret else data_name


class PoolHistory(View):
    """
    Display pool history balance changes for given user
    """
    def render(self):
        user = User.by_id(self.session,
                          int(self.request.matchdict['user_id']))

        if self.user.has_no_role:
            # can only see own requests
            if user.id != self.user.id:
                return HTTPFound(location=route_url('list_request',
                                                    self.request))

        if self.user.is_manager:
            # can only see own requests and managed user requests
            if ((user.id != self.user.id)
                    and (user.manager_id != self.user.id)):
                return HTTPFound(location=route_url('list_request',
                                                    self.request))

        today = datetime.now()
        year = int(self.request.params.get('year', today.year))

        start = datetime(2014, 5, 1)
        years = [item for item in reversed(range(start.year, today.year + 1))]

        pool_history = User.get_rtt_history(self.session, user, year)

        return {'user': user, 'year': year, 'years': years,
                'pool_history': pool_history}
