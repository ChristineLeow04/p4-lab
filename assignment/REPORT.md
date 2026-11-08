# Week 5 Assignment — Put a Limit on the Tunnel

**Student ID (last 4 digits only): 4965**

## 1. Analysis: the loop

I created a tunnel forwarding loop between the two P4 switches, s1 and s2. First, I started the network using `python3 topo.py --id 4965`. Then I checked the `MyIngress.myTunnel_exact` forwarding table on s2 and identified entry handle 2, which matched tunnel destination ID 3.

I changed this entry from output port 3 to port 1 using:

`sh ./p4cli s2 "table_modify MyIngress.myTunnel_exact MyIngress.myTunnel_forward 2 1"`

After that, I sent a tunnelled packet from h1 using:

`h1 python3 send.py 10.65.0.3 "loop" --tunnel 3`

Normally, s1 forwards tunnel destination 3 towards s2, while s2 forwards it towards h3. After modifying s2's table, both switches forwarded the packet towards each other, creating a forwarding loop.

In `evidence/loop-before.txt`, my first switch-log measurements were 821 packets for s1 and 820 for s2. Five seconds later, the recorded counts were 191 for s1 and 188 for s2. The logs showed substantial packet-processing activity, but the counts decreased instead of increasing. This could be caused by log truncation or other logging behavior, so these two measurements should not be treated as reliable cumulative packet counts.

The original tunnel forwarding action did not reduce the IPv4 TTL or enforce any tunnel hop limit. Therefore, a tunnelled packet could continue circulating without an explicit termination mechanism.

I also examined the ordinary IPv4 forwarding action in `tunnel.p4`. It decreases the IPv4 TTL by one whenever `ipv4_forward` runs. However, the program does not explicitly check whether TTL has reached zero before forwarding. Because TTL is stored as an unsigned 8-bit field, subtracting one from zero can cause it to wrap around to 255. Therefore, the original P4 code does not reliably stop an IPv4 forwarding loop at TTL zero. A proper implementation would check the TTL and drop expired packets.

## 2. Design: the hop limit

To prevent unlimited tunnel forwarding, I added an 8-bit `hop_limit` field to the custom tunnel header.

The updated header contains three fields:

| Field | Size | Purpose |
|---|---|---|
| `proto_id` | 16 bits | Identifies the protocol inside the tunnel |
| `dst_id` | 16 bits | Identifies the tunnel exit |
| `hop_limit` | 8 bits | Limits the number of forwarding operations |

The original header was 4 bytes, while the new header is 5 bytes.

I placed `hop_limit` after `dst_id` so the original two fields remain in their existing positions. This was intended to preserve the old switch's ability to identify the tunnel destination, although full compatibility with the new packet format was not guaranteed.

I chose a default starting hop limit of 4. This is sufficient for a normal packet to cross the two-switch network, while keeping the maximum permitted forwarding distance small enough to observe during loop testing. An 8-bit field supports values from 0 to 255 without adding too much header overhead.

The hop limit is checked inside the `myTunnel_forward` action in the ingress pipeline. When the value is greater than zero, the switch subtracts one and forwards the packet. When it is zero, the switch calls `mark_to_drop()` instead.

Checking for zero before subtraction prevents the value from wrapping around from 0 to 255.

The parser extracts the updated tunnel header, while the deparser emits the modified header before the IPv4 header. I also updated `send.py` and `receive.py` so both hosts understand the new field.

## 3. Implementation

I modified three source files to implement the hop-limit mechanism.

**tunnel.p4**

I added `bit<8> hop_limit` after the existing `dst_id` field in `myTunnel_t`. I also modified `myTunnel_forward` to check the remaining hop limit before forwarding.

When the field equals zero, the switch drops the packet. Otherwise, it decreases the value by one and forwards the packet through the selected port.

The parser and deparser continue to process the tunnel header, now including the additional byte. Ordinary IPv4 packets still use the separate `ipv4_lpm` forwarding table.

**send.py**

I added a Scapy `ByteField` to represent the 8-bit hop limit and introduced a `--hops` command-line argument.

The default value is 4. I also added input validation so users cannot supply values outside the valid range of 0 to 255.

For example:

`h1 python3 send.py 10.65.0.3 "delivery h2" --tunnel 2 --hops 4`

This sends a tunnelled packet with an initial hop limit of 4.

**receive.py**

I updated the receiver's Scapy tunnel-header definition to include the same `ByteField`.

The receiver now prints the remaining hop limit together with the tunnel destination ID, IPv4 addresses, TTL and received message.

All three files use the same header field order, which is necessary for correct packet encoding and decoding.

The updated P4 program compiled successfully, and both Python files passed syntax checking.

## 4. Evidence: it works

I tested normal tunnel delivery, ordinary IPv4 connectivity and loop termination.

**Tunnelled delivery to h2 and h3**

I started the network with `python3 topo.py --id 4965` and sent two packets with an initial hop limit of 4.

For h2, I used tunnel destination 2. The receiver reported:

`Received (tunnelled, dst_id=2, hop_limit=2) from 10.65.0.1 to 10.65.0.3, TTL 64: 'delivery h2'`

For h3, I used tunnel destination 3. The receiver reported:

`Received (tunnelled, dst_id=3, hop_limit=2) from 10.65.0.1 to 10.65.0.3, TTL 64: 'delivery h3'`

Both packets arrived at their intended tunnel exits. The remaining hop limit was 2, showing that the value decreased from 4 to 2 after passing through two P4 switches.

The IPv4 TTL remained at 64 because tunnel forwarding uses the custom hop-limit mechanism rather than the `ipv4_forward` action.

These results are recorded in `evidence/delivery.txt`.

**Ordinary IPv4 forwarding**

I also ran:

`h1 /bin/ping -c 2 10.65.0.3`

Both packets were received successfully, with 0% packet loss and TTL 62. This shows that the tunnel modification did not break ordinary IPv4 forwarding.

**Loop termination after the hop-limit implementation**

I restarted the network and repeated the forwarding-loop configuration from Section 1.

I sent the packet using:

`h1 python3 send.py 10.65.0.3 "limited loop" --tunnel 3 --hops 4`

The first switch-log measurements were 394 for s1 and 390 for s2. After five seconds, the counts remained 394 and 390.

Unlike the original experiment, the recorded packet-processing counts did not continue changing during the observation interval. This was consistent with the hop limit stopping the forwarding loop.

According to my design, a packet starting with a hop limit of 4 can be forwarded four times. The sequence should be s1 (4 to 3), s2 (3 to 2), s1 (2 to 1), and s2 (1 to 0). On the next visit to s1, the packet should be dropped.

The expected result is therefore four successful forwards followed by a drop on the fifth switch visit. The aggregate log counts support loop termination but do not independently prove the exact number of traversals.

The results are recorded in `evidence/loop-after.txt`.

## 5. Evaluation

### a) Mixed-version rollout

To evaluate compatibility, I ran s1 with the new `tunnel.p4` and s2 with the original `tunnel_v1.p4`:

`python3 topo.py --id 4965 --s2-program tunnel_v1.p4`

Before the experiment, I predicted that s2 would recognize the existing `proto_id` and `dst_id` fields because their positions had not changed. However, I expected possible parsing problems because s2 did not understand the additional hop-limit byte.

I first sent a packet to tunnel exit 2 with an initial hop limit of 4.

The packet successfully reached h2, and the receiver reported:

`Received (tunnelled, dst_id=2, hop_limit=3) from 10.65.0.1 to 10.65.0.3, TTL 64: 'mixed'`

This differed from a fully upgraded network, where the packet arrived with a remaining hop limit of 2.

The result shows that s1 decreased the hop limit once, while s2 did not decrease it. The existing tunnel destination fields still allowed forwarding to h2.

Because the new field was appended after the original four-byte header, the older switch did not understand it as a hop-limit field. The successful receiver output showed that the byte remained available to the updated receiver in this experiment, although this does not guarantee general compatibility between different header versions.

I also attempted to capture the packet using `tcpdump`, but both capture attempts reported zero packets. Therefore, I could not confirm the exact bytes through a successful hexadecimal capture. The receiver logs provide evidence of packet delivery, but I do not claim that the packet capture succeeded.

Next, I modified s2's tunnel destination 3 entry to output port 1, creating the same loop as before.

I sent a packet with an initial hop limit of 4. The switch-log counts were 401 for s1 and 396 for s2. After five seconds, the counts remained unchanged.

A detailed s1 switch log also showed a successful tunnel-table match, execution of `MyIngress.myTunnel_forward`, a call to `mark_to_drop(standard_metadata)`, and the packet being dropped at the end of ingress.

This supports the conclusion that the updated switch stopped the mixed-version loop through its hop-limit check.

However, the older s2 did not decrease the hop limit. Therefore, the packet could make more forwarding traversals than when both switches were upgraded.

A forwarding loop involving only old switches would not receive protection from this new mechanism.

The experiment results are recorded in `evidence/mixed.txt`.

### b) Safe deployment strategies

One possible rollout strategy is to introduce a new Ethernet type or an explicit protocol version for the updated tunnel header. This would allow switches to distinguish old and new packet formats before parsing them. During migration, compatible switches could support both formats and select the correct processing behavior.

A second strategy is to append new fields after existing fields, as I did in this assignment. This helps preserve the positions of the original fields, which may allow older switches to continue recognizing some forwarding information.

However, this approach is less reliable because old switches may misinterpret the extra bytes or fail to enforce the new behavior.

I would recommend using explicit version identification or a new EtherType for a real deployment. Switches should first be upgraded to understand both old and new formats. After confirming compatibility, senders can begin using the new format. The old format can be removed after all switches have been upgraded.

This provides a safer migration path than immediately introducing a new header into a network containing switches that do not understand it.

### c) Data-plane versus control-plane loop protection

The hop limit operates in the data plane. It checks individual packets while they pass through switches and prevents them from being forwarded indefinitely.

This is useful for protecting against forwarding loops that occur during actual operation, including loops caused by incorrect forwarding entries or unexpected changes.

In Week 3, I implemented `check_paths()` to validate static routes before deploying the network. This is a control-plane checking mechanism because it examines the configured forwarding paths and detects possible routing loops or missing routes before packets are sent.

The two approaches solve different problems.

`check_paths()` can identify mistakes in the known routing configuration before deployment, but it may not reflect later runtime changes or unexpected packet-processing behavior.

The hop limit can stop packets from circulating during operation, even when the forwarding configuration changes, but it does not identify or correct the underlying routing mistake.

Real networks benefit from both methods because pre-deployment validation reduces configuration errors, while data-plane protection limits the consequences of loops that still occur.

### d) Hardware implementation cost

The updated tunnel header adds one byte to every tunnelled packet. This creates a small increase in packet size and bandwidth usage.

On a hardware switch, the parser must extract an additional 8-bit field. The ingress pipeline also needs a comparison against zero, an 8-bit decrement operation and a conditional drop or forwarding decision.

These are relatively simple operations, but their actual performance cost depends on the switch architecture and available parser and pipeline resources.

On a programmable hardware switch with sufficient resources, the operations may fit within the existing processing stages without reducing line-rate forwarding performance. However, on more limited hardware, the additional parsing and processing requirements could affect resource utilization or require extra pipeline stages.

Overall, I consider the small header and processing overhead reasonable compared with the benefit of preventing uncontrolled tunnel forwarding loops.

## AI use

**AI tool used:** ChatGPT (OpenAI)  
**Permitted AI-use level:** Level 3 (Open)

I used ChatGPT to help understand P4 tunnel forwarding, the parser, ingress and deparser, and the purpose of a hop-limit mechanism.

ChatGPT assisted with developing and revising code for `tunnel.p4`, `send.py` and `receive.py`, explaining terminal errors and switch logs, interpreting experimental results, and organizing and drafting the assignment report.

I performed the network experiments using GitHub Codespaces, Mininet and BMv2, including the forwarding-loop tests, hop-limit delivery tests and mixed-version experiments. The results reported here are based on my own terminal outputs and observations.

I reviewed the suggested explanations and remain responsible for understanding, verifying and explaining the submitted implementation and report.

## Evidence files

| File | What it shows |
|---|---|
| `evidence/loop-before.txt` | Original tunnel loop configuration and initial switch-log measurements |
| `evidence/delivery.txt` | Successful tunnelled delivery to h2 and h3, received hop-limit values and ordinary IPv4 ping |
| `evidence/loop-after.txt` | Forwarding-loop experiment with hop-limit protection and stable log counts |
| `evidence/mixed.txt` | Mixed-version delivery, unsuccessful packet-capture attempts and loop termination evidence |
