"""IPNet: The Mininet that plays nice with IP networks.
This modules will auto-generate all needed configuration properties if
unspecified by the user"""
import math
from operator import attrgetter

from ipaddress import ip_network, ip_interface

from . import MIN_IGP_METRIC, OSPF_DEFAULT_AREA
from .utils import otherIntf, realIntfList, L3Router
from .router import Router
from .router.config import BasicRouterConfig
from .link import IPIntf, IPLink, PhysicalInterface

from mininet.net import Mininet
from mininet.node import Host
from mininet.nodelib import LinuxBridge
from mininet.log import lg as log


class IPNet(Mininet):
    """IPNet: An IP-aware Mininet"""
    def __init__(self,
                 router=Router,
                 config=BasicRouterConfig,
                 use_v4=True,
                 ipBase='192.168.0.0/16',
                 max_v4_prefixlen=24,
                 use_v6=True,
                 ip6Base='fc00::/7',
                 allocate_IPs=True,
                 max_v6_prefixlen=48,
                 igp_metric=MIN_IGP_METRIC,
                 igp_area=OSPF_DEFAULT_AREA,
                 link=IPLink,
                 intf=IPIntf,
                 switch=LinuxBridge,
                 controller=None,
                 *args, **kwargs):
        """Extends Mininet by adding IP-related ivars/functions and
        configuration knobs.


        :param router: The class to use to build routers
        :param config: The default configuration for the routers
        :param use_v4: Enable IPv4
        :param max_v4_prefixlen: The maximal IPv4 prefix for the auto-allocated
                                    broadcast domains
        :param use_v6: Enable IPv6
        :param ip6Base: Base prefix to use for IPv6 allocations
        :param max_v6_prefixlen: Maximal IPv6 prefixlen to auto-allocate
        :param allocate_IPs: wether to auto-allocate subnets in the network
        :param igp_metric: The default IGP metric for the links
        :param igp_area: The default IGP area for the links"""
        self.router = router
        self.config = config
        self.routers = []  # the list of router in the network
        self._ip_allocs = {}  # We need this to be able to do inverse-lookups
        self.max_v4_prefixlen = max_v4_prefixlen
        self._unallocated_ipbase = [ip_network(ipBase)]
        self.use_v4 = use_v4
        self.use_v6 = use_v6
        self.ip6Base = ip6Base
        self.max_v6_prefixlen = max_v6_prefixlen
        self._unallocated_ip6base = [ip_network(ip6Base)]
        self.broadcast_domains = None
        self.igp_metric = igp_metric
        self.igp_area = igp_area
        self.allocate_IPs = allocate_IPs
        self.physical_interface = {}  # itf: node
        super(IPNet, self).__init__(ipBase=ipBase, switch=switch, link=link,
                                    intf=intf, controller=controller,
                                    *args, **kwargs)

    def addRouter(self, name, cls=None, **params):
        """Add a router to the network

        :param name: the node name
        :param cls: the class to use to instantiate it"""
        defaults = {'use_v4': self.use_v4, 'use_v6': self.use_v6,
                    'config': self.config}
        defaults.update(params)
        if not cls:
            cls = self.router
        r = cls(name, **defaults)
        self.routers.append(r)
        self.nameToNode[name] = r
        return r

    def __iter__(self):
        for r in self.routers:
            yield r.name
        for n in super(IPNet, self).__iter__():
            yield n

    def __len__(self):
        return len(self.routers) + super(IPNet, self).__len__()

    def buildFromTopo(self, topo):
        log.info('\n*** Adding Routers:\n')
        for routerName in topo.routers():
            self.addRouter(routerName, **topo.nodeInfo(routerName))
            log.info(routerName + ' ')
        log.info('\n')
        self.physical_interface.update(topo.phys_interface_capture)
        super(IPNet, self).buildFromTopo(topo)

    def addLink(self, node1, node2,
                igp_metric=None, igp_area=None, igp_passive=False,
                *args, **params):
        """Register a link with additional properties

        :param igp_metric: the associated igp metric for this link
        :param igp_area: the associated igp area for this link
        :param igp_passive: whether IGP should create adjacencies over this
                            link or not"""
        # Handles defaults
        if not igp_metric:
            igp_metric = self.igp_metric
        if not igp_area:
            igp_area = self.igp_area
        # Register all link properties
        props = {'igp_metric': igp_metric,
                 'igp_area': igp_area,
                 'igp_passive': igp_passive}
        # Update interface properties with link properties
        for pstr in ('params1', 'params2'):
            try:
                p = params[pstr]
            except KeyError:
                p = params[pstr] = {}
            for k, v in props.iteritems():
                # Only iff not already specified
                if k not in p:
                    p[k] = v
        return super(IPNet, self).addLink(node1=node1, node2=node2,
                                          *args, **params)

    def node_for_ip(self, ip):
        """Return the node owning a given IP address

        :param ip: an IP address
        :return: a node name"""
        return self._ip_allocs[str(ip)]

    def start(self):
        super(IPNet, self).start()
        log.info('*** Starting, ', len(self.routers), 'routers\n')
        for router in self.routers:
            log.info(router.name + ' ')
            router.start()
        log.info('\n')
        log.info('*** Setting default host routes\n')
        for h in self.hosts:
            if 'defaultRoute' in h.params:
                continue  # Skipping hosts with explicit default route
            default = False
            # The first router we find will become the default gateway
            # TODO make it work with v6 as well
            for itf in realIntfList(h):
                for r in itf.broadcast_domain.routers:
                    log.info('%s via %s, ' % (h.name, r.name))
                    h.setDefaultRoute('via %s' % r.ip)
                    default = True
                    break
                if default:
                    break
            if not default:
                log.info('skipping %s , ' % h.name)
        log.info('\n')

    def stop(self):
        log.info('*** Stopping', len(self.routers),  'routers\n')
        for router in self.routers:
            log.info(router.name + ' ')
            router.terminate()
        log.info('\n')
        super(IPNet, self).stop()

    def build(self):
        super(IPNet, self).build()
        self.broadcast_domains = self._broadcast_domains()
        log.info("*** Found", len(self.broadcast_domains),
                 "broadcast domains\n")
        if self.allocate_IPs:
            self._allocate_IPs()
        # Physical interfaces are their own broadcast domain
        for itf_name, n in self.physical_interface.iteritems():
            try:
                itf = PhysicalInterface(itf_name, node=self[n])
                log.info('\n*** Adding Physical interface',
                         itf_name, 'to', n, '\n')
                self.broadcast_domains.append(BroadcastDomain(itf))
            except KeyError:
                log.error('!!! Node', n, 'not found!\n')
        try:
            self.topo.post_build(self)
        except AttributeError as e:
            log.error('*** Skipping post_build():', str(e), '\n')

    def _allocate_IPs(self):
        """Allocate IP addresses on every interface in every broadcast
        domain"""
        if self.use_v4:
            self._allocate_ipv4()
        if self.use_v6:
            self._allocate_ipv6()

    def _allocate_ipv4(self):
        log.info("*** Allocating IPv4 addresses\n")
        self._allocate_subnets(self._unallocated_ipbase,
                               self.broadcast_domains,
                               net_key='net',
                               size_key='max_v4prefixlen',
                               max_prefixlen=self.max_v4_prefixlen)
        for domain in self.broadcast_domains:
            for intf in domain:
                ip = domain.next_ipv4()
                intf.setIP(ip)
                self._ip_allocs[ip.with_prefixlen] = intf.node

    def _allocate_ipv6(self):
        log.info("*** Allocating IPv6 addresses\n")
        self._allocate_subnets(self._unallocated_ip6base,
                               self.broadcast_domains,
                               net_key='net6',
                               size_key='max_v6prefixlen',
                               max_prefixlen=self.max_v6_prefixlen)
        for domain in self.broadcast_domains:
            for intf in domain:
                ip = domain.next_ipv6()
                intf.setIP6(ip)
                self._ip_allocs[ip.with_prefixlen] = intf.node

    @staticmethod
    def _allocate_subnets(subnets, domains,
                          net_key='net', size_key='max_v4prefixlen',
                          max_prefixlen=24):
        """Allocate subnets to broadcast domains.

        We keep the subnets sorted as x < y wrt the available number of
        addressess in the subnet so that the bigger domains
        take the smallest subnets before subdividing them.
        As the domains range from the biggest to the smallest, and the subnets
        from the smallest to the biggest, the biggest domains will take the
        first subnet that is able to contain it, and split it in several
        subnets until it is restricted to its prefix.
        The next domain then is necessarily of the same size (reuses on of the
        split subnets) or smaller (uses a previsouly split subnet or splits a
        bigger one). This avoids wasting of addresses (wrt. the specified
        max_prefixlen) at the cost of a quadratic (?) behavior.

        :param subnets: a list of ip_network of available subnets. This list
                        will be modified to account for the new allocations.
        :param domains: a list of BroadcastDomain
        :param net_key: the key to use to set the allocated subnet in the
                        broadcast domain.
        :param size_key: the key to use to retrieve the maximal prefix length
                         suitable for a broadcast domain
        :param max_prefixlen: The maximal prefixlen that can be allocated,
                                e.g. to not allocate /126 for IPv6 P2P links
        :return: iterator of (domain, subnet)"""
        domains.sort(key=len, reverse=True)
        _prefixlen = attrgetter('prefixlen')
        subnets.sort(key=_prefixlen, reverse=True)
        for d in domains:
            if not subnets:
                raise ValueError('No subnet left in the prefix space for all'
                                 'broadcast domains.')
            plen = min(max_prefixlen, getattr(d, size_key))
            if plen < subnets[-1].prefixlen:
                raise ValueError('Could not find a subnet big enough for a '
                                 'broadcast domain.')
            log.debug('Allocating prefix', plen, 'for interfaces',
                      d.interfaces)
            # Try to find a suitable subnet in the list
            for i, net in enumerate(subnets):
                nets = []
                # if the subnet is too big for the prefix, perform a left
                # expansion (only expand one at a time to keep subnets as
                # aggregated as possible).
                while plen > net.prefixlen:
                    # Get list of subnets and append to list of previous
                    # expanded subnets as it is bigger wrt. prefixlen
                    net, next_net = tuple(net.subnets(prefixlen_diff=1))
                    nets.append(next_net)
                # Check if we have an appropriately-sized subnet
                if plen == net.prefixlen:
                    # Register the allocation
                    setattr(d, net_key, net)
                    # Delete the expanded/used subnet
                    del subnets[i]
                    # Insert the creadted subnets if any
                    subnets.extend(nets)
                    # Sort the array again
                    subnets.sort(key=_prefixlen, reverse=True)
                    # Proceed to the next broadcast domain
                    break
                # Otherwise try the next network for the current domain

    def _broadcast_domains(self):
        """Build the broadcast domains for this topology"""
        domains = []
        interfaces = {intf: False
                      for n in self.values()
                      if BroadcastDomain.is_domain_boundary(n)
                      for intf in realIntfList(n)}
        for intf, explored in interfaces.iteritems():
            # the interface already belongs to a broadcast domain
            if explored:
                continue
            # create a new domain and explore the interface
            bd = BroadcastDomain(intf)
            # Mark all explored interfaces belonging to that domain
            for i in bd:
                interfaces[i] = True
                i.broadcast_domain = bd
            domains.append(bd)
        return domains


class BroadcastDomain(object):
    """An IP broadcast domain in the network. This class stores the set of
    interfaces belonging to the same broadcast domain, as well as the
    associated IP prefix if any"""

    # The set of object that will define L3 domain boundaries
    # FIXME Where do we put middleboxes in this model ?
    BOUNDARIES = (Host, Router)

    def __init__(self, interfaces=None, *args, **kwargs):
        """Initialize the broadcast domain and optionally explore a set of
        interfaces

        :param interfaces: one Intf or a list of Intf"""
        super(BroadcastDomain, self).__init__(*args, **kwargs)
        self.interfaces = set()
        self.net = None
        self._allocated_v4 = 1  # We need to skip subnet address
        self.net6 = None
        # self._allocated_v6 = 0  # We can use the full address space
        self._allocated_v6 = 1  # FIXME null-addresses are routed directly
        # to the routers loopback .. Might be a bug in the netns code.
        if interfaces:
            if not isinstance(interfaces, list):
                interfaces = [interfaces]
            self.explore(interfaces)

    @staticmethod
    def is_domain_boundary(node):
        """Check whether the node is a L3 broadcast domain boundary

        :param node: a Node instance"""
        return isinstance(node, BroadcastDomain.BOUNDARIES)

    def __iter__(self):
        """Iterates over all interfaces in this broadcast domain"""
        return iter(self.interfaces)

    def __len__(self):
        """The number of interfaces in this broadcast domain"""
        return len(self.interfaces)

    def explore(self, itfs):
        """Explore a new list of interfaces and add them and their neightbors
        to this broadcast domain

        :param itf: a list of Intf"""
        while itfs:
            # Explore one element
            i = itfs.pop()
            if i in self.interfaces:
                continue
            if self.is_domain_boundary(i.node):
                self.interfaces.add(i)
            # check its corresponding interface
            other = otherIntf(i)
            if not other:  # This is an unbound interface
                continue
            # if it is a L3 boundary register it and stop there
            if self.is_domain_boundary(other.node):
                self.interfaces.add(other)
            else:
                # explode the node's interface to explore them
                itfs.extend([x for x in realIntfList(other.node)
                             if x is not other])

    @property
    def max_v4prefixlen(self):
        """Return the maximal IPv4 prefix suitable for this domain"""
        # IPv4 reserves 2 addresses for broadcast/subnet addresses
        return (32 - math.ceil(math.log(2 + len(self.interfaces), 2)))

    @property
    def max_v6prefixlen(self):
        """Return the maximal IPv6 prefix suitable for this domain"""
        # IPv6 can use whole subnet space for addressing
        return (128 - math.ceil(math.log(len(self.interfaces), 2)))

    @property
    def routers(self):
        """Yield all interfaces in this domain belonging to a L3 router"""
        return filter(L3Router.is_l3router_intf, self.interfaces)

    def next_ipv4(self):
        """Allocate and return the next available IPv4 address in this
        domain

        :return ip_interface:"""
        try:
            addr = self.net[self._allocated_v4]
            self._allocated_v4 += 1
            return ip_interface('%s/%d' % (addr, self.net.prefixlen))
        except IndexError:
            raise ValueError('No more available IPv4 address')
        except TypeError:
            raise ValueError('No associated IPv4 subnet')

    def next_ipv6(self):
        """Allocate and return the next available IPv6 address in this
        domain

        :return ip_interface:"""
        try:
            addr = self.net6[self._allocated_v6]
            self._allocated_v6 += 1
            return ip_interface('%s/%d' % (addr, self.net6.prefixlen))
        except IndexError:
            raise ValueError('No more available IPv6 address')
        except TypeError:
            raise ValueError('No associated IPv6 subnet')
