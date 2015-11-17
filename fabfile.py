import json
import multiprocessing
import re

import pika
from fabric.api import *

OS_TENANT_ID = 'fill in'
OS_CREDENTIALS_FILE = '~/devstack-admin-openrc.sh'
PHYSNET = 'physnet1'

env.hosts = ['127.0.0.1:2222']


@task
def setup():
    vagrant()
    local(
        'sed -i -E "s/\(OS_TENANT_ID=\).*/\\1%s/" '
        '~/devstack-admin-openrc.sh' % OS_TENANT_ID)
    zsh(
        'glance image-create --name esxi --disk-format vmdk '
        '--visibility public '
        '--file /Users/kendriu/Archive/vmware_mech_driver/TestESXi '
        '--progress --container-format bare')
    sudo('locale-gen pl_PL.UTF-8')
    with warn_only():
        sudo('pip uninstall --yes suds')
    sudo('pip install git+git://github.com/yunesj/suds')
    with cd('/opt/stack/vmware-dvs-ml2-driver'):
        sudo('pip install -e .')
    with cd('/etc/neutron'):
        stack(
            'sed -i -E "s/(nova_admin_tenant_id = ).*/\\1%s/" '
            'neutron.conf' % OS_TENANT_ID)
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
    result = zsh(
        'neutron net-create demo --tenant-id %s --provider:network_type vlan '
        '--provider:physical_network physnet1 --provider:segmentation_id '
        '204' % OS_TENANT_ID,
        capture=True)
    print result
    zsh('neutron subnet-create demo 192.168.0.0/24')
    net_id = re.search('id\s*\|\s*([^\s]*)\s', result).groups()[0]
    local('sed -i -E "s/\(net-id=\).\{36\}/\\1%s/" ~/.zshrc' % net_id)


@task
def sg():
    with warn_only():
        zsh('neutron security-group-delete sg')
    zsh('neutron security-group-create sg')


@task
def sgr():
    result = zsh('neutron security-group-show -f value sg', capture=True)
    for line in result.splitlines():
        if '"id"' in line:
            rule = json.loads(line)
            zsh('neutron security-group-rule-delete ' + rule['id'])
            break
    zsh('neutron security-group-rule-create sg')


@task
def test_network():
    with warn_only():
        net_id = zsh('neutron net-show bug_test -f value -c id', capture=True)
    if net_id:
        ports = zsh('neutron port-list -c id -f csv', capture=True)
        pool = multiprocessing.Pool(20)
        result = pool.map_async(delete_on_port,
                                (p.strip('"') for p in ports.splitlines()))
        result.wait()
        failing = True
        while failing:
            with warn_only():
                failing = zsh('neutron net-delete bug_test').failed

    result = zsh(
        'neutron net-create bug_test --tenant-id %s --provider:network_type'
        ' vlan --provider:physical_network %s --provider:segmentation_id '
        '204' % (OS_TENANT_ID, PHYSNET), capture=True)
    print result
    zsh('neutron subnet-create bug_test 192.168.2.0/24')
    net_id = re.search('id\s*\|\s*([^\s]*)\s', result).groups()[0]
    VMS = 3
    pool = multiprocessing.Pool(VMS)

    result = pool.map_async(boot, [net_id for _ in range(VMS)])
    result.wait()


@task
def delete_all_instances():
    result = zsh('nova list --minimal', capture=True)

    def devices():
        for line in result.splitlines():
            match = re.search(r'\|\s*([\dabcdef-]+)\s', line)
            if match:
                yield match.groups()[0]

    pool = multiprocessing.Pool(20)
    result = pool.map_async(delete_device, devices())
    result.wait()


@task
def delete_networks():
    exclude = ['private', 'public']
    result = zsh('neutron net-list -c id  -c name -f csv', capture=True)
    for line in result.splitlines()[1:]:
        id, name = line.split(',')
        id = id.strip('"')
        name = name.strip('"')
        if name not in exclude:
            with warn_only():
                zsh('neutron net-delete %s' % id)


@task
def cleanup():
    execute(delete_all_instances)
    execute(delete_networks)


@task
def hello_world():
    """Test of sending rpc messages"""
    payload = {'event_type': 'security_group_rule.create.end',
               'message_id': u'83c78255-024b-4627-b3fd-d9a6c691be0d',
               'payload': {
                   'security_group_rule': {
                       'direction': u'ingress',
                       'ethertype': 'IPv4',
                       'id': '287c8956-b726-4fd0-9226-c395bddd6f1a',
                       'port_range_max': None,
                       'port_range_min': None,
                       'protocol': None,
                       'remote_group_id': None,
                       'remote_ip_prefix': None,
                       'security_group_id': u'301b07aa-60a9-4922-'
                                            'b76d-4ce1ba46e679',
                       'tenant_id': u'0258e176b3be4a14949ee19f9c439a82'}},
               'priority': 'INFO',
               'publisher_id': 'network.manager',
               'timestamp': u'2015-06-03 10:37:57.561165'}

    connection = pika.BlockingConnection(pika.ConnectionParameters(
        host='192.168.51.10', port=5672))
    channel = connection.channel()

    channel.queue_declare(queue='hello.info')

    channel.basic_publish(exchange='',
                          routing_key='hello.info',
                          body=payload)
    print " [x] Sent 'Hello World!'"
    connection.close()


def vagrant():
    env.disable_known_hosts = True
    env.user = 'vagrant'
    result = local('vagrant ssh-config | grep IdentityFile', capture=True)
    env.key_filename = result.split()[1]


def stack(*args, **kwargs):
    kwargs['user'] = 'stack'
    return sudo(*args, **kwargs)


def zsh(cmd, *args, **kwargs):
    with prefix('source ' + OS_CREDENTIALS_FILE):
        return local(
            '/bin/zsh -c "source ~/.zshrc && workon devstack && %s"' % cmd,
            *args, **kwargs)


def boot(net_id):
    zsh(
        'nova boot --flavor m1.tiny --image esxi --nic net-id=%s esxi' % net_id
    )


def delete_on_port(port_id):
    with warn_only():
        device_id = zsh('neutron port-show %s -f value -c device_id' % port_id,
                        capture=True)
        delete_device(device_id)


def delete_device(device_id):
    if device_id:
        with warn_only():
            zsh('nova delete %s' % device_id)
