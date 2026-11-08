
#!/usr/bin/env python3
"""Send one UDP packet, optionally inside the custom tunnel header.

    python3 send.py <ip address> "<message>"
    python3 send.py <ip address> "<message>" --tunnel N
    python3 send.py <ip address> "<message>" --tunnel N --hops 4
"""
import argparse

from scapy.all import (
    IP, UDP, Ether, Packet, ShortField, ByteField,
    bind_layers, get_if_hwaddr, get_if_list, sendp
)


class MyTunnel(Packet):
    name = "MyTunnel"
    fields_desc = [
        ShortField("proto_id", 0x0800),
        ShortField("dst_id", 0),
        ByteField("hop_limit", 4),
    ]


bind_layers(Ether, MyTunnel, type=0x1212)
bind_layers(MyTunnel, IP, proto_id=0x0800)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("ip")
    ap.add_argument("message")
    ap.add_argument(
        "--tunnel",
        type=int,
        metavar="N",
        help="tunnel exit to deliver to"
    )
    ap.add_argument(
        "--hops",
        type=int,
        default=4,
        metavar="N",
        help="initial tunnel hop limit (0-255, default: 4)"
    )

    args = ap.parse_args()

    if not 0 <= args.hops <= 255:
        ap.error("--hops must be between 0 and 255")

    iface = next(i for i in get_if_list() if i.endswith("-eth0"))
    pkt = Ether(src=get_if_hwaddr(iface), dst="ff:ff:ff:ff:ff:ff")

    if args.tunnel is not None:
        pkt = pkt / MyTunnel(
            dst_id=args.tunnel,
            hop_limit=args.hops
        )

    pkt = pkt / IP(dst=args.ip) / UDP(
        sport=4321,
        dport=4321
    ) / args.message

    pkt.show2()
    sendp(pkt, iface=iface, verbose=False)
    print(f"Sent from {iface}.")


if __name__ == "__main__":
    main()
