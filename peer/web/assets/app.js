const statusEl = document.getElementById("status");
const peerListEl = document.getElementById("peerList");
const chatListEl = document.getElementById("chatList");
const chatSearchInput = document.getElementById("chatSearch");
const chatFeedEl = document.getElementById("chatFeed");
const metricsGridEl = document.getElementById("metricsGrid");
const chatForm = document.getElementById("chatForm");
const messageInput = document.getElementById("messageInput");
const sendButton = chatForm.querySelector("button[type=submit]");
const chatTitleEl = document.getElementById("chatTitle");
const chatSubtitleEl = document.getElementById("chatSubtitle");
const chatModeEl = document.getElementById("chatMode");
const peerGreetingEl = document.getElementById("peerGreeting");
const toastContainer = document.getElementById("toastContainer");

const refreshPeersBtn = document.getElementById("refreshPeers");
const refreshMetricsBtn = document.getElementById("refreshMetrics");
const navButtons = Array.from(document.querySelectorAll(".nav-button"));
const views = Array.from(document.querySelectorAll(".view"));

const newGroupBtn = document.getElementById("newGroup");
const groupModal = document.getElementById("groupModal");
const groupPeerList = document.getElementById("groupPeerList");
const groupNameInput = document.getElementById("groupNameInput");
const createGroupBtn = document.getElementById("createGroup");
const cancelGroupBtn = document.getElementById("cancelGroup");
const closeGroupBtn = document.getElementById("closeGroup");

let socket;
let peers = [];
let groups = [];
let selfPeerId = "peer";
let activeChat = { type: "direct", id: "", peerIds: [], label: "Chat" };
let openMenu = null;
const chatHistory = new Map();
const historyLoaded = new Set();
const recentDirectPeers = new Set();

function setStatus(text, ok) {
    statusEl.textContent = text;
    statusEl.classList.toggle("ok", ok);
}

function setPeerGreeting(peerId) {
    selfPeerId = peerId || "peer";
    if (peerGreetingEl) {
        peerGreetingEl.textContent = `Hello, ${selfPeerId}`;
    }
}

function setView(viewName) {
    views.forEach((view) => {
        const isActive = view.dataset.view === viewName;
        view.hidden = !isActive;
    });
    navButtons.forEach((button) => {
        button.classList.toggle("active", button.dataset.view === viewName);
    });
}

function loadGroups() {
    try {
        const raw = localStorage.getItem("p2p_groups");
        if (raw) {
            groups = JSON.parse(raw);
        }
    } catch (err) {
        groups = [];
    }
}

function saveGroups() {
    localStorage.setItem("p2p_groups", JSON.stringify(groups));
}

function chatKey(type, id) {
    return `${type}:${id}`;
}

function getHistory(type, id) {
    const key = chatKey(type, id);
    if (!chatHistory.has(key)) {
        chatHistory.set(key, []);
    }
    return chatHistory.get(key);
}

function hasInboundDirect(peerId) {
    if (recentDirectPeers.has(peerId)) {
        return true;
    }
    const history = chatHistory.get(chatKey("direct", peerId));
    if (!history || !history.length) {
        return false;
    }
    return history.some((item) => item.direction === "in");
}

function appendToHistory(type, id, message, direction) {
    if (!id) {
        return;
    }
    const history = getHistory(type, id);
    if (message?.message_id && history.some((item) => item.message_id === message.message_id)) {
        return;
    }
    history.push({ ...message, direction });
    if (history.length > 200) {
        history.shift();
    }
    if (activeChat.type === type && activeChat.id === id) {
        renderChatFeed();
    }
}

function mergeHistory(existing, incoming) {
    const merged = Array.isArray(existing) ? [...existing] : [];
    const seen = new Set(
        merged.map((item) => item.message_id || `${item.timestamp}|${item.from}|${item.text}|${item.direction}`),
    );
    (incoming || []).forEach((item) => {
        const key = item.message_id || `${item.timestamp}|${item.from}|${item.text}|${item.direction}`;
        if (!seen.has(key)) {
            merged.push(item);
            seen.add(key);
        }
    });
    merged.sort((a, b) => (a.timestamp || 0) - (b.timestamp || 0));
    if (merged.length > 200) {
        return merged.slice(-200);
    }
    return merged;
}

async function loadHistory(type, id) {
    if (!id) {
        return;
    }
    const key = chatKey(type, id);
    if (historyLoaded.has(key)) {
        return;
    }
    historyLoaded.add(key);
    try {
        const res = await fetch(`/api/history?chat_type=${encodeURIComponent(type)}&chat_id=${encodeURIComponent(id)}`);
        const data = await res.json();
        const current = getHistory(type, id);
        const merged = mergeHistory(current, Array.isArray(data) ? data : []);
        chatHistory.set(key, merged);
        if (merged.some((item) => item.direction === "in") && type === "direct") {
            recentDirectPeers.add(id);
        }
        if (activeChat.type === type && activeChat.id === id) {
            renderChatFeed();
        }
        renderChatList();
    } catch (err) {
        historyLoaded.delete(key);
    }
}

async function loadRecentDirectPeers() {
    try {
        const res = await fetch("/api/recent");
        const data = await res.json();
        const peersList = Array.isArray(data?.direct_peers) ? data.direct_peers : [];
        peersList.forEach((peerId) => {
            if (peerId) {
                recentDirectPeers.add(peerId);
            }
        });
        renderChatList();
    } catch (err) {
        return;
    }
}

function renderChatFeed() {
    chatFeedEl.innerHTML = "";
    if (!activeChat.id) {
        return;
    }
    const history = getHistory(activeChat.type, activeChat.id);
    history.forEach((item) => {
        addMessage(item, item.direction);
    });
}

function normalizeMembers(members) {
    const unique = new Set();
    members.forEach((member) => {
        if (member) {
            unique.add(member);
        }
    });
    return Array.from(unique).sort();
}

function buildGroupId(members) {
    const normalized = normalizeMembers(members);
    return normalized.length ? `group-${normalized.join("|")}` : "";
}

function createUniqueGroupId() {
    if (window.crypto && typeof window.crypto.randomUUID === "function") {
        return `group-${window.crypto.randomUUID()}`;
    }
    return `group-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
}

function buildGroupName(members) {
    const normalized = normalizeMembers(members);
    if (!normalized.length) {
        return "Group";
    }
    return `Group ${normalized.join(", ")}`;
}

function ensureGroupFromMessage(payload) {
    const recipients = Array.isArray(payload.recipients) ? payload.recipients : [];
    const members = normalizeMembers(recipients);
    if (members.length < 2) {
        return null;
    }
    const groupId = payload.group_id || buildGroupId(members);
    const existing = groups.find((group) => group.id === groupId);
    if (existing) {
        let updated = false;
        if ((payload.system || payload.kind === "group_init") && payload.group_name && payload.group_name !== existing.name) {
            existing.name = payload.group_name;
            updated = true;
        }
        const current = normalizeMembers(existing.peerIds);
        if (current.join("|") !== members.join("|")) {
            existing.peerIds = members;
            updated = true;
        }
        if (updated) {
            saveGroups();
            renderChatList();
        }
        return existing;
    }
    const newGroup = {
        id: groupId,
        name: payload.group_name || buildGroupName(members),
        peerIds: members,
    };
    groups = [newGroup, ...groups];
    saveGroups();
    renderChatList();
    return newGroup;
}

async function fetchPeers() {
    const res = await fetch("/api/peers");
    const data = await res.json();
    peers = data || [];
    renderPeersPage();
    renderChatList();
}

async function fetchMetrics() {
    const res = await fetch("/api/metrics");
    const data = await res.json();
    renderMetrics(data);
}

async function fetchIdentity() {
    try {
        const res = await fetch("/api/me");
        const data = await res.json();
        setPeerGreeting(data?.peer_id);
    } catch (err) {
        setPeerGreeting(selfPeerId);
    }
}

function renderPeersPage() {
    peerListEl.innerHTML = "";
    if (!peers.length) {
        const empty = document.createElement("li");
        empty.textContent = "No peers yet";
        empty.className = "empty";
        peerListEl.appendChild(empty);
        return;
    }

    peers.forEach((peer) => {
        const item = document.createElement("li");
        item.className = "peer-row";
        item.dataset.peerId = peer.peer_id;
        const badge = `badge ${peer.status}`;
        item.innerHTML = `
      <div>
        <p class="peer-id">${peer.peer_id}</p>
        <p class="peer-meta">${peer.host}:${peer.port}</p>
      </div>
      <div class="peer-actions">
        <span class="${badge}">${peer.status}</span>
        <button class="menu-button" aria-label="Actions">...</button>
        <div class="menu">
          <button class="menu-item" data-action="direct">Direct chat</button>
          <button class="menu-item" data-action="group">Group chat</button>
        </div>
      </div>
    `;

        const menuButton = item.querySelector(".menu-button");
        const menu = item.querySelector(".menu");
        const directButton = item.querySelector('[data-action="direct"]');
        const groupButton = item.querySelector('[data-action="group"]');

        menuButton.addEventListener("click", (event) => {
            event.stopPropagation();
            toggleMenu(menu);
        });

        directButton.addEventListener("click", () => {
            startDirectChat(peer.peer_id);
            closeMenu(menu);
        });

        groupButton.addEventListener("click", () => {
            openGroupModal([peer.peer_id]);
            closeMenu(menu);
        });

        peerListEl.appendChild(item);
    });
}

function renderChatList() {
    chatListEl.innerHTML = "";
    const items = [];
    groups.forEach((group) => {
        items.push({ type: "group", id: group.id, label: group.name, peerIds: group.peerIds });
    });
    peers.forEach((peer) => {
        if (!hasInboundDirect(peer.peer_id)) {
            return;
        }
        items.push({ type: "direct", id: peer.peer_id, label: peer.peer_id, peerIds: [peer.peer_id] });
    });

    const query = chatSearchInput.value.trim().toLowerCase();
    const filteredItems = query
        ? items.filter((item) => item.label.toLowerCase().includes(query))
        : items;

    if (!filteredItems.length) {
        const empty = document.createElement("li");
        empty.textContent = query ? "No matching chats" : "No chats yet";
        empty.className = "empty";
        chatListEl.appendChild(empty);
        return;
    }

    filteredItems.forEach((itemData) => {
        const item = document.createElement("li");
        item.className = "chat-item";
        item.dataset.type = itemData.type;
        item.dataset.id = itemData.id;
        const label = itemData.label;
        const subtitle = itemData.type === "group" ? `${itemData.peerIds.length} members` : "Direct";
        const tag = itemData.type === "group" ? "group" : "direct";
        item.innerHTML = `
      <div class="chat-meta">
        <span>${label}</span>
        <span class="subtle">${subtitle}</span>
      </div>
            <span class="chat-tag ${tag}">${itemData.type === "group" ? "Group" : "Direct"}</span>
    `;
        item.addEventListener("click", () => {
            item.classList.remove("flash");
            if (itemData.type === "group") {
                startGroupChat(itemData);
            } else {
                startDirectChat(itemData.id);
            }
        });
        chatListEl.appendChild(item);
    });
    highlightActiveChat();
}

function flashChatItem(type, id) {
    if (!id) {
        return;
    }
    if (activeChat.type === type && activeChat.id === id) {
        return;
    }
    const selector = `.chat-item[data-id="${id}"][data-type="${type}"]`;
    let item = document.querySelector(selector);
    if (!item) {
        renderChatList();
        item = document.querySelector(selector);
    }
    if (!item) {
        return;
    }
    if (!item.classList.contains("flash")) {
        item.classList.add("flash");
    }
}

function renderMetrics(metrics) {
    metricsGridEl.innerHTML = "";
    Object.entries(metrics || {}).forEach(([key, value]) => {
        const card = document.createElement("div");
        card.className = "metric";
        card.innerHTML = `
      <p class="metric-label">${key.replaceAll("_", " ")}</p>
      <p class="metric-value">${value}</p>
    `;
        metricsGridEl.appendChild(card);
    });
}

function addMessage(message, direction = "in") {
    const row = document.createElement("div");
    row.className = `bubble ${direction}`;
    const timestamp = message.timestamp ? new Date(message.timestamp * 1000) : new Date();
    row.innerHTML = `
    <div class="bubble-header">
      <span>${message.from || "peer"}</span>
      <span>${timestamp.toLocaleTimeString()}</span>
    </div>
    <div class="bubble-body">${message.text}</div>
  `;
    chatFeedEl.appendChild(row);
    chatFeedEl.scrollTop = chatFeedEl.scrollHeight;
}

function showToast(text, variant = "info") {
    const toast = document.createElement("div");
    toast.className = `toast ${variant}`;
    const content = document.createElement("div");
    content.textContent = text;
    const close = document.createElement("button");
    close.type = "button";
    close.textContent = "x";
    close.addEventListener("click", () => toast.remove());
    toast.appendChild(content);
    toast.appendChild(close);
    toastContainer.appendChild(toast);
    setTimeout(() => toast.remove(), 4000);
}

async function sendMessage(peerIds, text, meta = {}) {
    const isGroup = peerIds.length > 1 || meta.type === "group";
    const endpoint = isGroup ? "/api/group" : "/api/chat";
    const payload = isGroup
        ? { peer_ids: peerIds, message: text }
        : { peer_id: peerIds[0], message: text };

    if (isGroup && meta.id) {
        payload.group_id = meta.id;
    }
    if (meta.system) {
        payload.system = true;
        payload.kind = meta.kind || "system";
        if (meta.label) {
            payload.group_name = meta.label;
        }
    }
    try {
        const res = await fetch(endpoint, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload),
        });
        const data = await res.json().catch(() => ({}));
        return { ok: res.ok && data.ok !== false, error: data.error };
    } catch (err) {
        return { ok: false, error: err?.message || "Network error" };
    }
}

function connectWebSocket() {
    const protocol = window.location.protocol === "https:" ? "wss" : "ws";
    socket = new WebSocket(`${protocol}://${window.location.host}/ws`);
    socket.onopen = () => setStatus("Connected", true);
    socket.onclose = () => setStatus("Disconnected", false);
    socket.onerror = () => setStatus("Error", false);
    socket.onmessage = (event) => {
        const data = JSON.parse(event.data);
        if (data.type === "peers_snapshot") {
            peers = data.payload || [];
            renderPeersPage();
            renderChatList();
        }
        if (data.type === "metrics_snapshot") {
            renderMetrics(data.payload);
        }
        if (data.type === "message_received") {
            const payload = data.payload || {};
            const isGroup = payload.chat_type === "group" || (Array.isArray(payload.recipients) && payload.recipients.length > 1);
            const isSynced = Boolean(payload.synced);
            if (isGroup) {
                const group = ensureGroupFromMessage(payload);
                if (group && !payload.system && !isSynced) {
                    flashChatItem("group", group.id);
                }
                if (group && !payload.system) {
                    appendToHistory("group", group.id, payload, "in");
                }
            } else if (payload.from) {
                if (!payload.system) {
                    appendToHistory("direct", payload.from, payload, "in");
                    if (!isSynced) {
                        flashChatItem("direct", payload.from);
                    }
                }
            }
            if (!payload.system) {
                highlightActiveChat();
            }
        }
        if (data.type === "message_sent") {
            const payload = data.payload || {};
            const isGroup = payload.chat_type === "group";
            if (isGroup) {
                const group = ensureGroupFromMessage(payload);
                if (group && !payload.system) {
                    appendToHistory("group", group.id, payload, "out");
                }
            } else if (payload.to) {
                if (!payload.system) {
                    appendToHistory("direct", payload.to, payload, "out");
                }
            }
        }
        if (data.type === "message_failed") {
            showToast("Send failed.", "error");
        }
        if (data.type === "peer_update") {
            fetchPeers();
        }
    };
}

function startDirectChat(peerId) {
    // clear composer if switching to a different direct chat
    if (activeChat.type !== "direct" || activeChat.id !== peerId) {
        messageInput.value = "";
    }
    activeChat = { type: "direct", id: peerId, peerIds: [peerId], label: peerId };
    updateChatHeader();
    updateComposerState(true);
    highlightActiveChat();
    setView("chat");
    loadHistory("direct", peerId);
    renderChatFeed();
    messageInput.focus();
}

function startGroupChat(group) {
    // clear composer if switching to a different group chat
    if (activeChat.type !== "group" || activeChat.id !== group.id) {
        messageInput.value = "";
    }
    activeChat = {
        type: "group",
        id: group.id,
        peerIds: group.peerIds,
        label: group.name,
    };
    updateChatHeader();
    updateComposerState(true);
    highlightActiveChat();
    setView("chat");
    loadHistory("group", group.id);
    renderChatFeed();
    messageInput.focus();
}

function updateChatHeader() {
    chatTitleEl.textContent = activeChat.label || "Chat";
    const subtitle =
        activeChat.type === "group"
            ? `${activeChat.peerIds.length} members`
            : activeChat.id
                ? `Direct with ${activeChat.id}`
                : "Select a peer or group to start.";
    chatSubtitleEl.textContent = subtitle;
    chatModeEl.textContent = activeChat.type === "group" ? "Group" : "Direct";
}

function updateComposerState(enabled) {
    messageInput.disabled = !enabled;
    sendButton.disabled = !enabled;
    messageInput.placeholder = enabled ? "Type a message" : "Select a chat to start";
}

function highlightActiveChat() {
    document.querySelectorAll(".chat-item").forEach((item) => {
        item.classList.remove("active");
    });
    if (activeChat.type === "direct" && activeChat.id) {
        const chatItem = document.querySelector(`.chat-item[data-id="${activeChat.id}"][data-type="direct"]`);
        if (chatItem) {
            chatItem.classList.add("active");
            chatItem.classList.remove("flash");
        }
    }
    if (activeChat.type === "group" && activeChat.id) {
        const chatItem = document.querySelector(`.chat-item[data-id="${activeChat.id}"][data-type="group"]`);
        if (chatItem) {
            chatItem.classList.add("active");
            chatItem.classList.remove("flash");
        }
    }
}

function toggleMenu(menu) {
    if (openMenu && openMenu !== menu) {
        openMenu.classList.remove("open");
    }
    menu.classList.toggle("open");
    openMenu = menu.classList.contains("open") ? menu : null;
}

function closeMenu(menu) {
    if (menu) {
        menu.classList.remove("open");
    }
    openMenu = null;
}

function openGroupModal(preselected = []) {
    groupNameInput.value = "";
    groupPeerList.innerHTML = "";
    peers.forEach((peer) => {
        const label = document.createElement("label");
        const checkbox = document.createElement("input");
        checkbox.type = "checkbox";
        checkbox.value = peer.peer_id;
        if (preselected.includes(peer.peer_id)) {
            checkbox.checked = true;
        }
        label.appendChild(checkbox);
        label.appendChild(document.createTextNode(peer.peer_id));
        groupPeerList.appendChild(label);
    });
    groupModal.classList.add("open");
    groupModal.setAttribute("aria-hidden", "false");
}

function closeGroupModal() {
    groupModal.classList.remove("open");
    groupModal.setAttribute("aria-hidden", "true");
}

function createGroup() {
    const selected = Array.from(groupPeerList.querySelectorAll("input[type=checkbox]:checked"))
        .map((input) => input.value)
        .filter(Boolean);

    if (selected.length < 2) {
        showToast("Select at least 2 peers for group chat.", "warning");
        return;
    }

    const members = normalizeMembers(selected);
    const groupId = createUniqueGroupId();
    const name = groupNameInput.value.trim() || buildGroupName(members);
    const newGroup = {
        id: groupId,
        name,
        peerIds: members,
    };
    groups = [newGroup, ...groups];
    saveGroups();
    renderChatList();
    closeGroupModal();
    startGroupChat(newGroup);
    announceGroup(newGroup);
}

async function announceGroup(group) {
    const peerIds = group.peerIds;
    if (!peerIds.length) {
        return;
    }
    await sendMessage(peerIds, "", {
        type: "group",
        id: group.id,
        label: group.name,
        system: true,
        kind: "group_init",
    });
}

chatForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    const text = messageInput.value.trim();
    const peerIds = activeChat.peerIds;
    if (!peerIds.length || !text) {
        showToast("Peer ID and message are required.", "warning");
        return;
    }
    const result = await sendMessage(peerIds, text, activeChat);
    if (!result.ok) {
        showToast(result.error || "Send failed.", "error");
        return;
    }
    messageInput.value = "";
    messageInput.focus();
});

messageInput.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey) {
        event.preventDefault();
        chatForm.requestSubmit();
    }
});

document.addEventListener("click", (event) => {
    if (openMenu && !openMenu.contains(event.target)) {
        closeMenu(openMenu);
    }
});

newGroupBtn.addEventListener("click", () => openGroupModal());
createGroupBtn.addEventListener("click", createGroup);
cancelGroupBtn.addEventListener("click", closeGroupModal);
closeGroupBtn.addEventListener("click", closeGroupModal);

refreshPeersBtn.addEventListener("click", fetchPeers);
refreshMetricsBtn.addEventListener("click", fetchMetrics);

navButtons.forEach((button) => {
    button.addEventListener("click", () => setView(button.dataset.view));
});

chatSearchInput.addEventListener("input", renderChatList);

loadGroups();
setPeerGreeting(selfPeerId);
setView("chat");
updateChatHeader();
updateComposerState(false);
fetchIdentity();
loadRecentDirectPeers();
fetchPeers();
fetchMetrics();
connectWebSocket();
