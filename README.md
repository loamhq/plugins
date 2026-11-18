# Loam's own plugins

First-party plugins for [Loam](https://github.com/loamhq/loam), each in its
own folder, each listed in the [marketplace](https://github.com/loamhq/marketplace).
Install one from Settings › Market in your Loam, or from Settings › Plugins
with its link.

| Plugin | Kind | What it does |
|---|---|---|
| [`images`](images/) | extension | Your agents make and change pictures with OpenAI or Google, saved in your vault |
| [`syncthing`](syncthing/) | extension | Your vault syncs with your own devices over Syncthing, and your agents pair a new one |
| [`autumn`](autumn/) | theme | Golden leaves in daylight, a red maple at dusk |
| [`deep-forest`](deep-forest/) | theme | The fern floor and soft canopy light of an old forest |
| [`foundry`](foundry/) | theme | Steel, concrete and the patina of a working foundry, with tighter corners |
| [`mist-trail`](mist-trail/) | theme | Waterfall spray, fir shade and cedar trunks along a forest trail |
| [`yosemite`](yosemite/) | theme | Granite walls, meadow grasses and national park signage |

**Versions are tags, one plugin each:** `<id>-v<version>`, e.g.
`images-v1.1.0`. A link to install is
`https://github.com/loamhq/plugins/tree/<id>-v<version>/<id>`.

**Where they are made.** Each folder is published, unchanged, from
`plugins/_listed/<id>/` in [loamhq/loam](https://github.com/loamhq/loam),
which holds their tests and history; change them there.

Code is under the AGPL-3.0 ([LICENSE](LICENSE)), as Loam is. Each theme's
display font is under the SIL Open Font License, in that theme's `OFL.txt`.
