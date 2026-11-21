#!/usr/bin/env python3
"""Minimal Ryu OpenFlow 1.3 learning switch for the Mininet demo."""
import sys
try:
    import eventlet.wsgi
    if not hasattr(eventlet.wsgi, 'ALREADY_HANDLED'):
        eventlet.wsgi.ALREADY_HANDLED = 'ALREADY_HANDLED'
except ImportError:
    pass

from ryu.base import app_manager
from ryu.controller import ofp_event
from ryu.controller.handler import CONFIG_DISPATCHER, MAIN_DISPATCHER, set_ev_cls
from ryu.ofproto import ofproto_v1_3
from ryu.lib.packet import packet, ethernet, ether_types, ipv4, udp

class HealthMonitorSwitch(app_manager.RyuApp):
    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]

    def __init__(self, *args, **kwargs):
        super(HealthMonitorSwitch, self).__init__(*args, **kwargs)
        self.mac_to_port = {}

    @set_ev_cls(ofp_event.EventOFPSwitchFeatures, CONFIG_DISPATCHER)
    def switch_features_handler(self, ev):
        datapath = ev.msg.datapath
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser
        match = parser.OFPMatch()
        actions = [parser.OFPActionOutput(ofproto.OFPP_CONTROLLER,
                                          ofproto.OFPCML_NO_BUFFER)]
        self.add_flow(datapath, 0, match, actions)
        self.logger.info("Installed table-miss rule on switch %s", datapath.id)

    def add_flow(self, datapath, priority, match, actions, buffer_id=None):
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser
        instructions = [parser.OFPInstructionActions(ofproto.OFPIT_APPLY_ACTIONS, actions)]
        kwargs = {"datapath": datapath, "priority": priority,
                  "match": match, "instructions": instructions}
        if buffer_id is not None:
            kwargs["buffer_id"] = buffer_id
        datapath.send_msg(parser.OFPFlowMod(**kwargs))

    @set_ev_cls(ofp_event.EventOFPPacketIn, MAIN_DISPATCHER)
    def packet_in_handler(self, ev):
        msg = ev.msg
        datapath = msg.datapath
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser
        in_port = msg.match["in_port"]
        pkt = packet.Packet(msg.data)
        eth = pkt.get_protocol(ethernet.ethernet)
        if eth is None or eth.ethertype == ether_types.ETH_TYPE_LLDP:
            return

        dst, src = eth.dst, eth.src
        dpid = datapath.id
        self.mac_to_port.setdefault(dpid, {})
        self.mac_to_port[dpid][src] = in_port
        out_port = self.mac_to_port[dpid].get(dst, ofproto.OFPP_FLOOD)
        actions = [parser.OFPActionOutput(out_port)]

        ip_pkt = pkt.get_protocol(ipv4.ipv4)
        udp_pkt = pkt.get_protocol(udp.udp)
        is_monitor = (ip_pkt is not None and udp_pkt is not None
                      and udp_pkt.dst_port == 9999)
        if is_monitor:
            self.logger.info("Monitoring UDP flow observed: %s -> %s, dst_port=9999",
                             ip_pkt.src, ip_pkt.dst)

        buffer_id = (msg.buffer_id if msg.buffer_id != ofproto.OFP_NO_BUFFER else None)

        if out_port != ofproto.OFPP_FLOOD:
            if is_monitor:
                match = parser.OFPMatch(in_port=in_port, eth_src=src, eth_dst=dst,
                                        eth_type=ether_types.ETH_TYPE_IP,
                                        ip_proto=17, udp_dst=9999)
                self.add_flow(datapath, 100, match, actions, buffer_id)
            else:
                match = parser.OFPMatch(in_port=in_port, eth_src=src, eth_dst=dst)
                self.add_flow(datapath, 10, match, actions, buffer_id)
            if buffer_id is not None:
                return   # the FlowMod already forwarded the buffered packet

        data = msg.data if msg.buffer_id == ofproto.OFP_NO_BUFFER else None
        out = parser.OFPPacketOut(datapath=datapath, buffer_id=msg.buffer_id,
                                  in_port=in_port, actions=actions, data=data)
        datapath.send_msg(out)
