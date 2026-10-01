# qwen2.5-coder:14b  (templates: 68/100 · raw: 65/100)

| mode | case | score | tries | time | verdict | notes |
|---|---|---|---|---|---|---|
| templates | screw | 100 | 1 | 59s | ok | template: Screw / bolt |
| templates | hex nut | 100 | 1 | 53s | ok | template: Nut |
| templates | washer | 100 | 1 | 34s | ok | template: Washer |
| templates | knob | 100 | 1 | 47s | ok | from scratch |
| templates | spur gear | 0 | 3 | 217s | did not build | from scratch; build failed: TypeError: 'float' object cannot be interpreted as an integer; build failed: TypeError: 'float' object cannot be interpreted as an integer; build failed: TypeError: 'float' object cannot be interpreted as an integer |
| templates | phone stand | 70 | 1 | 72s | dimension base_height is drawn 0 but the parameter is 10; dimension arm_length is drawn 50 but the parameter is 100 | from scratch |
| templates | cable clip | 70 | 1 | 90s | dimension head_height is drawn 5 but the parameter is 5.3 | template: Screw / bolt |
| templates | wall hook | 0 | 3 | 394s | did not build | template: Screw / bolt; build failed: Part.OCCError: ExtendCurveToPoint; build failed: Part.OCCError: ExtendCurveToPoint; build failed: Part.OCCError: ExtendCurveToPoint |
| raw | screw | 70 | 1 | 64s | part: 2 separate solids (expected 1); dimension diameter is drawn 4 but the parameter is 8 | from scratch |
| raw | hex nut | 70 | 1 | 32s | doesn't look like the part | from scratch |
| raw | washer | 100 | 1 | 43s | ok | from scratch |
| raw | knob | 70 | 2 | 109s | dimension chamfer_radius is drawn 0 but the parameter is 2 | from scratch; build failed: AttributeError: module 'Part' has no attribute 'makeChamfer' |
| raw | spur gear | 0 | 3 | 177s | did not build | from scratch; build failed: TypeError: 'float' object cannot be interpreted as an integer; build failed: TypeError: 'float' object cannot be interpreted as an integer; build failed: TypeError: 'float' object cannot be interpreted as an integer |
| raw | phone stand | 70 | 2 | 213s | part: 2 separate solids (expected 1); part: mesh not watertight (bodies touching only along an edge?) | from scratch; build failed: KeyError: 'phone_mount_length' |
| raw | cable clip | 70 | 1 | 82s | dimension screw_diameter is drawn 1.5 but the parameter is 3 | from scratch |
| raw | wall hook | 70 | 1 | 66s | dimension screw_hole_diameter is drawn 0 but the parameter is 3; dimension screw_hole_distance is drawn 60 but the parameter is 20 | from scratch |
