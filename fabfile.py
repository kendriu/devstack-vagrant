import re
from StringIO import StringIO
from contextlib import nested
from fabric.api import *

VMWARE_DRIVER_REPO = 'https://github.com/Mirantis/vmware-dvs.git'
VMWARE_DRIVER_BRANCH = 'sg_new_engine'

PHYSNET = 'physnet1'

env.hosts = ['127.0.0.1:2222']


@task
def setup():
    vagrant()
    with cd('/home/stack'):
        stack('wget -qO ./TestESXi.vmdk '
              'https://www.googledrive.com/host/0B7JeSl37_w2KaVIycHBmeWt2cGM')
        stack('glance image-create '
              '--name esxi '
              '--disk-format vmdk '
              '--is-public True '
              '--file ./TestESXi.vmdk '
              '--container-format bare')
    with warn_only():
        sudo('pip uninstall --yes suds')
    sudo('pip install git+git://github.com/yunesj/suds')

    with nested(warn_only(), cd('/opt/stack')):
        stack('git clone -qb {} -- {} vmware-dvs-ml2-driver'.format(
            VMWARE_DRIVER_BRANCH, VMWARE_DRIVER_REPO))
    with cd('/opt/stack/vmware-dvs-ml2-driver'):
        sudo('pip install -e .')
    id_ = tenant_id('admin')
    with cd('/etc/neutron'):
        stack('sed -i -E "s/(nova_admin_tenant_id = ).*/\\1%s/" '
              'neutron.conf' % id_)
    with cd('/etc/neutron/plugins/ml2'):
        stack(
            'sed -i -E "s/(mechanism_drivers = ).*/\\1vmware_dvs/" '
            'ml2_conf.ini')
        stack(
            'sed -i -E "s/(tenant_network_types = ).*/\\1vlan/" '
            'ml2_conf.ini')
        stack(
            'sed -i -E "s/(type_drivers = ).*/\\1vlan/" '
            'ml2_conf.ini')


@task
def network():
    vagrant()
    id_ = tenant_id('admin')
    stack(
        'neutron net-create demo --tenant-id %s --provider:network_type vlan '
        '--provider:physical_network physnet1 --provider:segmentation_id '
        '204' % id_)
    stack('neutron subnet-create demo 192.168.0.0/24')


def tenant_id(name):
    io = StringIO()
    stack('keystone tenant-get {}'.format(name), stdout=io)
    details = io.getvalue()
    print details
    return re.search('id\W*(\w*)', details, re.MULTILINE).groups()[0]


def vagrant():
    env.disable_known_hosts = True
    env.user = 'vagrant'
    result = local('vagrant ssh-config | grep IdentityFile', capture=True)
    env.key_filename = result.split()[1]


def stack(cmd, *args, **kwargs):
    kwargs['user'] = 'stack'
    cmd = 'source ~/devstack/openrc && ' + cmd
    with shell_env(OS_TENANT_NAME='admin', OS_USERNAME='admin'):
        return sudo(cmd, *args, **kwargs)
