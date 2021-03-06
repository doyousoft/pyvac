# -*- coding: utf-8 -*-

import os
import sys

from pyramid.paster import get_appsettings, setup_logging

from pyvac.helpers.sqla import create_engine, dispose_engine
from pyvac.models import (
    DBSession, Base, Permission, Group, User, VacationType, Countries,
)


def usage(argv):
    cmd = os.path.basename(argv[0])
    print('usage: %s <config_uri>\n'
          '(example: "%s development.ini")' % (cmd, cmd))
    sys.exit(1)


def populate(engine):

    Base.metadata.create_all(engine)
    session = DBSession()

    user_perm = Permission(name=u'user_view')
    admin_perm = Permission(name=u'admin_view')
    manager_perm = Permission(name=u'manager_view')
    sudo_perm = Permission(name=u'sudo_view')
    session.add(user_perm)
    session.add(admin_perm)
    session.add(manager_perm)
    session.add(sudo_perm)

    admin_group = Group(name=u'admin')
    admin_group.permissions.append(user_perm)
    admin_group.permissions.append(admin_perm)
    admin_group.permissions.append(manager_perm)
    admin_group.permissions.append(sudo_perm)
    session.add(admin_group)

    manager_group = Group(name=u'manager')
    manager_group.permissions.append(user_perm)
    manager_group.permissions.append(manager_perm)
    manager_group.permissions.append(sudo_perm)
    session.add(manager_group)

    user_group = Group(name=u'user')
    user_group.permissions.append(user_perm)
    session.add(user_group)

    sudoer_group = Group(name=u'sudoer')
    sudoer_group.permissions.append(sudo_perm)
    session.add(sudoer_group)

    vactype1 = VacationType(name=u'CP')
    session.add(vactype1)
    vactype2 = VacationType(name=u'RTT')
    session.add(vactype2)
    vactype3 = VacationType(name=u'Congé Parental')
    session.add(vactype3)
    vactype4 = VacationType(name=u'Récupération')
    session.add(vactype4)
    vactype5 = VacationType(name=u'Maladie', visibility=u'admin')
    session.add(vactype5)
    vactype6 = VacationType(name=u'Exceptionnel')
    session.add(vactype6)

    fr_country = Countries(name=u'fr')
    session.add(fr_country)
    lu_country = Countries(name=u'lu')
    session.add(lu_country)
    us_country = Countries(name=u'us')
    session.add(us_country)
    zh_country = Countries(name=u'zh')
    session.add(zh_country)

    # CP is available for everyone
    vactype1.countries.append(fr_country)
    vactype1.countries.append(lu_country)
    vactype1.countries.append(us_country)
    vactype1.countries.append(zh_country)

    # RTT only available for france
    vactype2.countries.append(fr_country)

    # Parental vacation is available for everyone
    vactype3.countries.append(fr_country)
    vactype3.countries.append(lu_country)
    vactype3.countries.append(us_country)
    vactype3.countries.append(zh_country)

    # Recovery is available for everyone
    vactype4.countries.append(fr_country)
    vactype4.countries.append(lu_country)
    vactype4.countries.append(us_country)
    vactype4.countries.append(zh_country)

    # Sickness vacation is available for all countries
    vactype5.countries.append(fr_country)
    vactype5.countries.append(lu_country)
    vactype5.countries.append(us_country)
    vactype5.countries.append(zh_country)

    # Exception vacation is available for all countries
    vactype6.countries.append(fr_country)
    vactype6.countries.append(lu_country)
    vactype6.countries.append(us_country)
    vactype6.countries.append(zh_country)

    common_password = u'changeme'

    admin = User(login=u'admin',
                 password=common_password,
                 email=u'root@localhost.localdomain',
                 firstname=u'The',
                 lastname=u'Administrator',
                 role=u'admin',
                 _country=fr_country)
    admin.groups.append(admin_group)
    session.add(admin)

    session.commit()


def main(argv=sys.argv):
    if len(argv) != 2:
        usage(argv)
    config_uri = argv[1]
    setup_logging(config_uri)
    settings = get_appsettings(config_uri)
    engine = create_engine('pyvac', settings, scoped=False)
    populate(engine)
    dispose_engine('pyvac')
