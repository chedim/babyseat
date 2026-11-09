"""#980 rows 26-27,39: dial values as seed-pack data (generated once; edit the values here, not the modules)."""

DATA = {'consequences': {'HORIZONS': [3600.0, 86400.0, 259200.0],
                  'SNAP_KEYS': ['state',
                                'value',
                                'status',
                                'online',
                                'enabled',
                                'fulfilled_ts',
                                'blocked_until'],
                  'WRITE_TOOLS': ['exec',
                                  'actuate',
                                  'entity',
                                  'edit',
                                  'learn',
                                  'forget',
                                  'memory',
                                  'intend',
                                  'goal',
                                  'watch',
                                  'script',
                                  'method',
                                  'fulfill']},
 'attention': {'ALERT_EXEMPT': ['intent-planted', 'intent-landed']},
 'claims': {'ACTION_WINDOW': [-2700, 300], 'RETRIEVAL_WINDOW': [-600, 120], 'TESTS_WINDOW': 3600},
 'toybox': {'KINDS': {'fast': {'about': 'changes on its own clock faster than self can look: every look '
                                        "finds it changed, none of it self's doing",
                               'params': {'every': 5.0},
                               'clock': True},
                      'slow': {'about': 'changes once a period (an hour by default): long runs of identical '
                                        'looks',
                               'params': {'period': 3600.0},
                               'clock': True},
                      'burst': {'about': 'bursty: some windows are active and it changes every few seconds '
                                         'inside them, the rest it sits still',
                                'params': {'window': 600.0, 'active': 0.35, 'every': 45.0},
                                'clock': True},
                      'drift': {'about': 'a bounded random walk that steps at random intervals of its own, '
                                         'whether anyone looks or not',
                                'params': {'min_s': 45.0, 'max_s': 600.0, 'step': 5, 'lo': 0, 'hi': 100},
                                'clock': True},
                      'die': {'about': 'a memoryless change: probability p per second, on its own clock, '
                                       'never on a read',
                              'params': {'p': 0.016666666666666666, 'faces': 6},
                              'clock': True},
                      'body': {'about': 'changes only because self looked at it -- the body/other test case',
                               'params': {},
                               'clock': False},
                      'tower': {'about': 'n blocks: self topples a block (and the ones above it) by nudging '
                                         'it, and lifts a fallen one by nudging it; a look changes nothing',
                                'params': {'n': 5},
                                'clock': False},
                      'ball': {'about': "a ball self and the other roll back and forth (rung 7's turns, "
                                        '#264): self rolls it to you by nudging it; roll it to self whenever '
                                        'you like, whichever side it is on',
                               'params': {},
                               'clock': False},
                      'mirror': {'about': "self's mirror (rung 9, #312): the glass shows self's own moves a "
                                          "moment late, and a stranger beside it shows self's moves from "
                                          'half an hour ago -- the same moves, only the glass follows what '
                                          'self does now',
                                 'params': {'delay': 1, 'lag_s': 1870.0, 'jitter': 0.1},
                                 'clock': False}},
            'ACTIONS': {'tower': ['knock', 'rebuild'],
                        'die': ['roll'],
                        'drift': ['nudge'],
                        'burst': ['nudge'],
                        'ball': ['roll']}}}
