from datetime import timedelta

from .schemas import MemoryConfig, DEFAULT_MAINTENANCE_PROMPT
from .store import iso, new_id, utcnow


def create_space(store, name, description=""):
    space_id = new_id("space")
    space = store.put("space", {"id": space_id, "name": name, "description": description, "created_at": iso()})
    config = MemoryConfig().model_dump()
    config["maintenance_prompt"] = DEFAULT_MAINTENANCE_PROMPT
    store.put("config", dict(config, id=space_id), space_id)
    store.put("model", {"id": space_id, "mode": "local", "base_url": "https://api.openai.com/v1", "model": ""}, space_id)
    return space


def seed_demo(store):
    if store.list("space"):
        return
    now = utcnow()
    space = create_space(store, "日常记忆", "一组虚构的示例经历，供你观察事件、锚点与时间如何共同唤起记忆。")
    sid = space["id"]
    me = store.put("person", {"id": new_id("person"), "name": "我", "relation": "日常对话", "space_id": sid}, sid)
    friend = store.put("person", {"id": new_id("person"), "name": "小林", "relation": "朋友 · 示例人物", "space_id": sid}, sid)
    convo = store.put("conversation", {"id": new_id("conv"), "space_id": sid, "person_id": me["id"], "title": "和记忆打个招呼", "created_at": iso(now), "demo": True}, sid)
    templates = [
        ("第一次烤面包：陨石一号", 172, .96, "生日那天，用户第一次在家烤面包。用户说成品外面焦黑、里面还没熟，并自己给它取名「陨石一号」。\n\n当时双方围绕这个称呼聊了很久。助手接了一句「烤箱这次算航天设备了」，用户又把失败的照片称为「登陆现场」。这些是实际出现在示例对话中的玩笑。\n\n用户最后说，以后还想再试一次。目前这件事本身已经结束，后续见关联事件。", {"time": ["生日", "第一次庆祝"], "scene": ["在家过生日", "烤面包", "烤箱"], "details": ["陨石一号", "登陆现场", "外焦内生"], "structure": ["认真准备的第一次尝试失败，产生了共同玩笑"], "triggers": ["下次生日想在家过", "再试试烤箱"]}, "双方反复接梗，用户主动给成品命名并分享照片；这是共同玩笑的起点。"),
        ("第二次尝试，终于能吃了", 155, .82, "用户再次尝试烤面包。这次降低了温度，并说「至少能咬动了」。\n\n用户主动提到了第一次的「陨石一号」，把这次叫作「陨石二号返航」。双方确认这次虽然外形不好看，但已能正常食用。\n\n这是第一次烘焙失败的后续，不能把两次尝试合并成一次。", {"time": ["周末"], "scene": ["第二次烘焙", "调整温度"], "details": ["陨石二号返航", "至少能咬动了"], "structure": ["失败后再次尝试，有了小小进展"], "triggers": ["再试一次", "面包终于成功"]}, "用户主动重提旧梗，形成了明确的连续故事。"),
        ("雨夜面馆，多送的一颗蛋", 12, .81, "用户加班后遇到大雨，临时走进一家快打烊的面馆。老板没有催促，还额外放了一颗卤蛋。\n\n用户原话是「本来只是想随便吃点，结果被这一颗蛋救了一下」。用户没有说明店名，也没有说自己以后经常去。\n\n这段对话围绕陌生人的细小善意展开，双方把那家店暂时叫作「雨夜补给站」。", {"time": ["加班后", "快打烊"], "scene": ["下雨", "面馆", "晚饭"], "details": ["多送一颗卤蛋", "雨夜补给站"], "structure": ["疲惫时被陌生人的小举动照顾"], "triggers": ["今天又下雨了", "想吃一碗热面", "一点小事让我缓过来了"]}, "用户明确描述小举动对自己的影响，并与助手形成了一个新的称呼。"),
        ("找了一整圈，眼镜就在领口", 46, .74, "用户说自己找眼镜，把桌面、沙发和包都翻遍了，最后发现眼镜一直挂在领口。\n\n用户把这次经历形容成「全屋搜救，原地结案」。助手接了这个玩笑。没有证据表明用户经常忘东西，不应由此推断其长期性格。", {"time": ["出门前"], "scene": ["找东西", "家里"], "details": ["眼镜挂在领口", "全屋搜救，原地结案"], "structure": ["费力排查后发现忽略了眼前的简单情况"], "triggers": ["折腾半天发现插座没开", "答案一直就在眼前"]}, "出现了鲜明的反转，用户自己概括了一句玩笑。"),
        ("昨天中午吃了份盖饭", 1, .10, "用户随口说中午点了一份普通的盖饭，随后就转去聊别的话题。\n\n没有提到店名、具体菜品或特别的感受。这里只记录明确出现的信息，不补充细节。", {"time": ["昨天中午"], "scene": ["午饭", "外卖"], "details": ["一份盖饭"], "structure": [], "triggers": []}, "一句带过，没有明确的额外关注信号。"),
        ("那次临时改道，反而看到了海", 91, .88, "示例人物小林提起，两人一次旅行错过了原定班车，临时换了路线，最后到了一处没有列在计划里的海边。\n\n小林当时说「计划没了，海倒是有了」。这件事来自小林的转述，不能改写成当前用户自己说过的经历。", {"time": ["旅行途中"], "scene": ["改计划", "班车", "海边"], "details": ["计划没了，海倒是有了"], "structure": ["原计划被打乱，意外遇到新的收获"], "triggers": ["原来的安排泡汤了", "临时换个地方"]}, "小林多次围绕计划外的收获展开，留下了一句明确原话。"),
        ("去年买的一根充电线", 320, .12, "用户说自己买了一根新的充电线，没有提及品牌、价格或购买原因。这段话没有继续展开。", {"time": [], "scene": ["买东西"], "details": ["充电线"], "structure": [], "triggers": []}, "没有特别的关注信号，属于普通生活片段。")
    ]
    events = []
    for index, (title, days, salience, body, anchors, reason) in enumerate(templates):
        when = now - timedelta(days=days)
        person_id = friend["id"] if index == 5 else me["id"]
        source = store.put("message", {"id": new_id("msg"), "space_id": sid, "conversation_id": convo["id"], "person_id": person_id, "role": "user", "content": body, "at": iso(when), "processed": True, "fixture": True, "client_id": None}, sid)
        events.append(store.put("event", {"id": new_id("event"), "space_id": sid, "title": title, "body": body, "occurred_at": iso(when), "learned_at": iso(when), "updated_at": iso(now), "person_ids": [person_id], "salience": salience, "salience_reason": reason, "anchors": anchors, "links": [], "source_ids": [source["id"]], "reinforcements": [], "status": "closed", "origin": "demo"}, sid))
    events[1]["links"] = [{"target_id": events[0]["id"], "relation": "第一次尝试的后续", "strength": .96}]
    events[1]["body"] += "\n\n前次经历：[[" + events[0]["id"] + "]]"
    store.put("event", events[1], sid)
    store.put("message", {"id": new_id("msg"), "space_id": sid, "conversation_id": convo["id"], "person_id": me["id"], "role": "user", "content": "下次生日想在家过，做点什么好呢？", "at": iso(now - timedelta(minutes=3)), "processed": True, "fixture": False, "example": True, "client_id": None}, sid)
    store.put("message", {"id": new_id("msg"), "space_id": sid, "conversation_id": convo["id"], "person_id": me["id"], "role": "assistant", "content": "还想挑战一下烤箱吗？你给第一块面包起的名字，我倒是一直记得——「陨石一号」。", "at": iso(now - timedelta(minutes=3) + timedelta(seconds=1)), "processed": True, "fixture": False, "example": True, "client_id": None, "response_mode": "fixture"}, sid)
