# Small Opera topology

`opera_16tor_4host_15us.txt` is a deterministic small dynamic Opera topology
for focused Flare experiments:

```text
16 ToRs
4 hosts per ToR (64 hosts total)
4 rotor uplinks per ToR
16 superslices / 48 topology slices
12.88 us epsilon + 0.62 us delta + 1.0 us reconfiguration
```

The topology is generated without MATLAB:

```bash
python3 topologies/opera_dynexp_topo_gen/generate_small_opera.py \
  --output topologies/opera_16tor_4host_15us.txt
```

The generator constructs a one-factorization, searches deterministic rotor
assignments whose three-active-rotor reconfiguration graphs are connected,
writes one shortest label-switched path for every ordered ToR pair in every
slice, and validates every emitted route against the slice adjacency.

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
  -topfile topologies/opera_16tor_4host_15us.txt
```

Host IDs are `0..63`. Hosts `4*t..4*t+3` attach to ToR `t`.

A balanced one-flow-per-host Flare experiment for this topology is available
under `run/opera_16tor_uniform/`.
