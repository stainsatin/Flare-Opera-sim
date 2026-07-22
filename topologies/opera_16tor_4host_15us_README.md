# Small Opera topologies

The repository contains two timing variants of the same deterministic small
dynamic Opera topology:

```text
opera_16tor_4host_15us.txt: 12.88 us epsilon + 0.62 us delta + 1.0 us reconfiguration = 14.5 us
opera_16tor_4host_55us.txt: 53.38 us epsilon + 0.62 us delta + 1.0 us reconfiguration = 55.0 us
```

Both variants have:

```text
16 ToRs
4 hosts per ToR (64 hosts total)
4 rotor uplinks per ToR
16 superslices / 48 topology slices
```

The topologies are generated without MATLAB:

```bash
python3 topologies/opera_dynexp_topo_gen/generate_small_opera.py \
  --output topologies/opera_16tor_4host_15us.txt

python3 topologies/opera_dynexp_topo_gen/generate_small_opera.py \
  --epsilon-ps 53380000 \
  --output topologies/opera_16tor_4host_55us.txt
```

The generator constructs a one-factorization, searches deterministic rotor
assignments whose three-active-rotor reconfiguration graphs are connected,
writes one shortest label-switched path for each unordered ToR pair, installs
its exact reverse for the opposite direction, and validates every emitted
route against the slice adjacency. Therefore, for every ToR pair and slice:

```text
route(A, B, slice) = reverse(route(B, A, slice))
```

The 55 us variant provides substantially more time for a credit and its
authorized data to traverse an unchanged topology. It reduces cross-superslice
risk but does not mathematically guarantee that a packet can never cross a
boundary when queues are unbounded or heavily congested.

Use the dynamic Opera executable, not the static GraphTopology executable:

```bash
src/opera/datacenter/htsim_xpass_dynexpTopology \
  -flare \
  -simtime 0.5 \
  -cwnd 40 \
  -q 600 \
  -credq 16 \
  -qshaping 8 \
  -tent 2 \
  -aeolus 8 \
  -winit 1.0 \
  -tloss 0.1 \
  -fbw 1.2 \
  -probfile run/pfun_exp2.txt \
  -utiltime 0.1 \
  -flowfile PATH_TO_64_HOST_FLOW_FILE.htsim \
  -topfile topologies/opera_16tor_4host_55us.txt
```

Host IDs are `0..63`. Hosts `4*t..4*t+3` attach to ToR `t`.

A balanced one-flow-per-host Flare experiment for this topology is available
under `run/opera_16tor_uniform/`.
