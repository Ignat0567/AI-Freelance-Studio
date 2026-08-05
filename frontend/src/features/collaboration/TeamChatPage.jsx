import React, { useEffect, useState } from 'react';
import { collaborationApi } from './collaborationApi.js';
import { useChannelMessages } from './useChannelMessages.js';
import './TeamChat.css';

function cleanError(err) {
  if (!err) return 'Something went wrong.';
  return err.message || 'The local backend rejected the request.';
}

function formatTime(isoString) {
  try {
    return new Date(isoString).toLocaleTimeString();
  } catch {
    return '';
  }
}

export default function TeamChatPage({ active }) {
  const [channels, setChannels] = useState([]);
  const [channelId, setChannelId] = useState('general');
  const [sender, setSender] = useState('You');
  const [draft, setDraft] = useState('');
  const [sending, setSending] = useState(false);
  const [error, setError] = useState('');
  const [events, setEvents] = useState([]);

  useEffect(() => {
    if (!active) return;
    collaborationApi.getChannels()
      .then(result => setChannels(result.channels || []))
      .catch(err => setError(cleanError(err)));
  }, [active]);

  const { messages, error: pollError } = useChannelMessages({ channelId, enabled: active });

  useEffect(() => {
    if (!active || !channelId) return undefined;
    let cancelled = false;
    const timer = window.setInterval(() => {
      collaborationApi.getEvents(channelId)
        .then(result => { if (!cancelled) setEvents(result.events || []); })
        .catch(() => {});
    }, 2000);
    collaborationApi.getEvents(channelId).then(result => { if (!cancelled) setEvents(result.events || []); }).catch(() => {});
    return () => { cancelled = true; window.clearInterval(timer); };
  }, [active, channelId]);

  if (!active) return null;

  const handleSend = async (event) => {
    event.preventDefault();
    const text = draft.trim();
    if (!text || sending) return;
    setSending(true);
    setError('');
    try {
      await collaborationApi.postMessage(channelId, { sender: sender.trim() || 'You', message: text });
      setDraft('');
    } catch (err) {
      setError(cleanError(err));
    } finally {
      setSending(false);
    }
  };

  return (
    <div className="tc-page">
      <section className="tc-hero">
        <div>
          <h2>Team Chat</h2>
          <p>Watch agents and the timeline of work as it happens, channel by channel.</p>
        </div>
      </section>

      <div className="tc-layout">
        <nav className="tc-channels">
          {channels.map(channel => (
            <button
              key={channel.id}
              type="button"
              className={`tc-channel-btn${channel.id === channelId ? ' tc-channel-btn-active' : ''}`}
              onClick={() => setChannelId(channel.id)}
            >
              # {channel.label}
            </button>
          ))}
        </nav>

        <div className="tc-main">
          <div className="tc-messages">
            {messages.length === 0 && <p className="tc-empty">No messages in this channel yet.</p>}
            {messages.map(message => (
              <div className={`tc-message tc-message-${message.sender_kind}`} key={message.id}>
                <div className="tc-message-meta">
                  <strong>{message.sender}</strong>
                  <span>{formatTime(message.created_at)}</span>
                </div>
                <p>{message.message}</p>
              </div>
            ))}
          </div>

          {(error || pollError) && <p className="tc-error">{error || pollError}</p>}

          <form className="tc-composer" onSubmit={handleSend}>
            <input
              className="tc-sender-input"
              value={sender}
              onChange={event => setSender(event.target.value)}
              maxLength={80}
              aria-label="Your name"
            />
            <input
              className="tc-message-input"
              value={draft}
              onChange={event => setDraft(event.target.value)}
              placeholder={`Message #${channelId}`}
              maxLength={4000}
            />
            <button type="submit" disabled={sending || !draft.trim()}>Send</button>
          </form>
        </div>

        <aside className="tc-timeline">
          <h3>Timeline</h3>
          {events.length === 0 && <p className="tc-empty">No activity yet.</p>}
          <ul className="tc-timeline-list">
            {events.slice().reverse().map(item => (
              <li key={item.id}>
                <span className="tc-timeline-kind">{item.kind.replace(/_/g, ' ')}</span>
                {item.agent && <span className="tc-timeline-agent">{item.agent}</span>}
                <span className="tc-timeline-message">{item.message}</span>
                <span className="tc-timeline-time">{formatTime(item.created_at)}</span>
              </li>
            ))}
          </ul>
        </aside>
      </div>
    </div>
  );
}
