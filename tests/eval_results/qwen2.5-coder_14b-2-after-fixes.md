# qwen2.5-coder:14b  (templates: 96/100)

| mode | case | score | tries | time | verdict | notes |
|---|---|---|---|---|---|---|
| templates | screw | 100 | 3 | 264s | ok | template: Screw / bolt; 1 check(s) failed; 1 check(s) failed |
| templates | hex nut | 100 | 1 | 30s | ok | template: Nut |
| templates | washer | 100 | 1 | 46s | ok | template: Washer |
| templates | knob | 100 | 1 | 55s | ok | from scratch |
| templates | spur gear | 100 | 1 | 40s | ok | from scratch |
| templates | phone stand | 70 | 3 | 334s | part: 2 separate solids (expected 1): make the pieces overlap by 0.1 mm and fuse() them into one; dimension stand_diameter is drawn 100 but the parameter is 10 (the two points in dimensions() must be exactly that far apart) | from scratch; 2 check(s) failed; 2 check(s) failed; 2 check(s) failed |
| templates | cable clip | 100 | 1 | 68s | ok | from scratch |
| templates | wall hook | 100 | 2 | 152s | ok | from scratch; 2 check(s) failed |
