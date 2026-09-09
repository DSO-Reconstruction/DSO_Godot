extends SceneTree
## Verify res://test.tscn: contents, textures, animations and above all
## ORIENTATION (a character should be taller than it is wide).

func _init() -> void:
	if not ResourceLoader.exists("res://test.tscn"):
		print("[verify] test.tscn missing")
		quit(1)
		return
	var root: Node = (load("res://test.tscn") as PackedScene).instantiate()
	get_root().add_child(root)

	var meshes: Array[MeshInstance3D] = []
	var skels: Array[Skeleton3D] = []
	var players: Array[AnimationPlayer] = []
	var parts: Array[GPUParticles3D] = []
	_walk(root, meshes, skels, players, parts)

	var textured := 0
	for m in meshes:
		if m.mesh == null:
			continue
		for si in m.mesh.get_surface_count():
			var mat = m.mesh.surface_get_material(si)
			if mat is BaseMaterial3D and (mat as BaseMaterial3D).albedo_texture != null:
				textured += 1
				break
	print("[verify] meshes=%d (%d textured) skeletons=%d players=%d particles=%d" % [
		meshes.size(), textured, skels.size(), players.size(), parts.size()])

	var anims := 0
	var looped := 0
	for p in players:
		for a in p.get_animation_list():
			anims += 1
			if p.get_animation(a).loop_mode != Animation.LOOP_NONE:
				looped += 1
	print("[verify] animations=%d (%d looping)" % [anims, looped])

	# Orientation, group by group.
	for child in root.get_children():
		if not (child is Node3D):
			continue
		var sub: Array[MeshInstance3D] = []
		var s2: Array[Skeleton3D] = []
		var p2: Array[AnimationPlayer] = []
		var q2: Array[GPUParticles3D] = []
		_walk(child, sub, s2, p2, q2)
		if sub.is_empty():
			continue
		var box := AABB()
		var first := true
		for m in sub:
			if m.mesh == null:
				continue
			var b: AABB = m.global_transform * m.mesh.get_aabb()
			box = b if first else box.merge(b)
			first = false
		if first:
			continue
		var sz := box.size
		var verdict := "-"
		if not s2.is_empty():
			verdict = "upright" if sz.y >= maxf(sz.x, sz.z) * 0.55 else "LYING DOWN?"
		print("[verify]   %-28s meshes=%-4d aabb=(%.1f x %.1f x %.1f) base_y=%.2f %s" % [
			child.name, sub.size(), sz.x, sz.y, sz.z, box.position.y, verdict])

	_check_motion(root)
	print("[verify] done")
	quit()


## Play each animation and measure how much the bone pose changes.
## We compare position AND rotation AND scale: skeletal animation is mostly
## rotation, so looking at positions alone gave a false "static" verdict.
func _check_motion(root: Node) -> void:
	var players: Array[AnimationPlayer] = []
	var m0: Array[MeshInstance3D] = []
	var s0: Array[Skeleton3D] = []
	var p0: Array[GPUParticles3D] = []
	_walk(root, m0, s0, players, p0)
	for ap in players:
		var skel: Skeleton3D = null
		var n: Node = ap.get_parent()
		while n != null and skel == null:
			skel = _find_skel(n)
			n = n.get_parent()
		if skel == null:
			continue
		var names := ap.get_animation_list()
		if names.is_empty():
			continue
		var anim_name := String(ap.autoplay)
		if anim_name.is_empty() or not (anim_name in names):
			anim_name = names[0]
		var anim := ap.get_animation(anim_name)
		var nb := skel.get_bone_count()

		ap.play(anim_name)
		ap.advance(0.0)
		ap.seek(0.0, true)
		var p0v: Array[Vector3] = []
		var r0v: Array[Quaternion] = []
		var s0v: Array[Vector3] = []
		for b in nb:
			p0v.append(skel.get_bone_pose_position(b))
			r0v.append(skel.get_bone_pose_rotation(b))
			s0v.append(skel.get_bone_pose_scale(b))

		var moved_pos := 0
		var moved_rot := 0
		var moved_scl := 0
		var best_pos := 0.0
		var best_rot := 0.0
		var seen := {}
		for k in 8:
			ap.seek(anim.length * float(k + 1) / 8.0, true)
			for b in nb:
				var dp: float = skel.get_bone_pose_position(b).distance_to(p0v[b])
				var q: Quaternion = skel.get_bone_pose_rotation(b)
				var dr: float = absf(r0v[b].angle_to(q))
				var ds: float = skel.get_bone_pose_scale(b).distance_to(s0v[b])
				best_pos = maxf(best_pos, dp)
				best_rot = maxf(best_rot, dr)
				if dp > 0.0005 and not seen.has("p%d" % b):
					seen["p%d" % b] = 1
					moved_pos += 1
				if dr > 0.001 and not seen.has("r%d" % b):
					seen["r%d" % b] = 1
					moved_rot += 1
				if ds > 0.0005 and not seen.has("s%d" % b):
					seen["s%d" % b] = 1
					moved_scl += 1
		var live: bool = (moved_pos + moved_rot + moved_scl) > 0
		print("[verify]   anim '%s' (%.2fs, %d tracks): moving bones pos=%d rot=%d scale=%d / %d" % [
			anim_name, anim.length, anim.get_track_count(),
			moved_pos, moved_rot, moved_scl, nb])
		print("[verify]      max travel %.4f, max rotation %.2f deg -> %s" % [
			best_pos, rad_to_deg(best_rot), ("ANIMATED" if live else "STATIC?")])


func _find_skel(n: Node) -> Skeleton3D:
	if n is Skeleton3D:
		return n
	for c in n.get_children():
		var r := _find_skel(c)
		if r != null:
			return r
	return null


func _walk(n: Node, meshes: Array[MeshInstance3D], skels: Array[Skeleton3D],
		players: Array[AnimationPlayer], parts: Array[GPUParticles3D]) -> void:
	if n is MeshInstance3D:
		meshes.append(n)
	elif n is Skeleton3D:
		skels.append(n)
	elif n is AnimationPlayer:
		players.append(n)
	elif n is GPUParticles3D:
		parts.append(n)
	for c in n.get_children():
		_walk(c, meshes, skels, players, parts)
