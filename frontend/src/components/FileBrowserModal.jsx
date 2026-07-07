import React, { useState, useEffect, useCallback } from 'react';

const DIR_COLOR = '#f59e0b';
const FILE_COLORS = {
  image: '#10b981',
  audio: '#8b5cf6',
  video: '#ef4444',
  archive: '#f59e0b',
  code: '#0ea5e9',
  document: '#6366f1',
  other: '#94a3b8',
};

function fileType(name, isDir) {
  if (isDir) return 'dir';
  const ext = name.split('.').pop().toLowerCase();
  if (['jpg','jpeg','png','gif','webp','svg','ico','bmp','tiff'].includes(ext)) return 'image';
  if (['mp3','wav','ogg','flac','aac','wma','m4a'].includes(ext)) return 'audio';
  if (['mp4','avi','mkv','mov','wmv','webm'].includes(ext)) return 'video';
  if (['zip','rar','7z','tar','gz','bz2','tgz','tbz2'].includes(ext)) return 'archive';
  if (['py','js','jsx','ts','tsx','html','css','json','xml','yaml','yml','toml','ini','cfg','sh','bat','ps1'].includes(ext)) return 'code';
  if (['md','txt','pdf','doc','docx','xls','xlsx','ppt','pptx','rtf'].includes(ext)) return 'document';
  return 'other';
}

function fileIcon(name, isDir) {
  if (isDir) return '📁';
  const t = fileType(name, false);
  const icons = { image: '🖼️', audio: '🎵', video: '🎬', archive: '📦', code: '📄', document: '📝', other: '📎' };
  return icons[t] || '📎';
}

function formatSize(bytes) {
  if (bytes === 0) return '';
  const units = ['B', 'KB', 'MB', 'GB'];
  let i = 0;
  let size = bytes;
  while (size >= 1024 && i < 3) { size /= 1024; i++; }
  return size.toFixed(i > 0 ? 1 : 0) + ' ' + units[i];
}

function formatDate(ts) {
  return new Date(ts * 1000).toLocaleDateString();
}

export default function FileBrowserModal({ activePort, projectId, projectTitle, onClose, addLog }) {
  const [entries, setEntries] = useState([]);
  const [currentPath, setCurrentPath] = useState('');
  const [parentPath, setParentPath] = useState('');
  const [loading, setLoading] = useState(true);
  const [uploading, setUploading] = useState(false);
  const [dragging, setDragging] = useState(false);
  const [newDirName, setNewDirName] = useState('');
  const [showNewDir, setShowNewDir] = useState(false);

  const fetchFiles = useCallback((relPath) => {
    setLoading(true);
    const params = relPath ? `?path=${encodeURIComponent(relPath)}` : '';
    fetch(`http://localhost:${activePort}/api/projects/${projectId}/files${params}`)
      .then(r => r.json())
      .then(data => {
        setEntries(data.entries || []);
        setCurrentPath(data.path || '');
        setParentPath(data.parent_path || '');
        setLoading(false);
      })
      .catch(() => { setEntries([]); setLoading(false); });
  }, [activePort, projectId]);

  useEffect(() => { fetchFiles(''); }, [fetchFiles]);
  useEffect(() => {
    const interval = setInterval(() => { fetchFiles(currentPath); }, 3000);
    return () => clearInterval(interval);
  }, [fetchFiles, currentPath]);

  const handleUpload = (fileList) => {
    if (!fileList || fileList.length === 0) return;
    setUploading(true);
    const form = new FormData();
    for (const f of fileList) form.append('files', f);
    fetch(`http://localhost:${activePort}/api/projects/${projectId}/files/upload`, {
      method: 'POST',
      body: form,
    })
      .then(r => r.json())
      .then(data => {
        addLog(`[Files]: Uploaded ${data.uploaded_count} file(s)${data.error_count > 0 ? `, ${data.error_count} errors` : ''}.`);
        fetchFiles(currentPath);
        setUploading(false);
      })
      .catch(() => { addLog('[Files]: Upload failed.'); setUploading(false); });
  };

  const handleArchiveUpload = (file) => {
    if (!file) return;
    setUploading(true);
    const form = new FormData();
    form.append('file', file);
    fetch(`http://localhost:${activePort}/api/projects/${projectId}/files/upload/archive`, {
      method: 'POST',
      body: form,
    })
      .then(r => r.json())
      .then(data => {
        addLog(`[Files]: Extracted ${data.extracted_count} item(s) from archive.`);
        fetchFiles(currentPath);
        setUploading(false);
      })
      .catch(() => { addLog('[Files]: Archive upload failed.'); setUploading(false); });
  };

  const handleDelete = (name, isDir) => {
    const relPath = currentPath ? `${currentPath}/${name}` : name;
    if (!confirm(`Delete ${isDir ? 'folder' : 'file'} "${name}"?`)) return;
    fetch(`http://localhost:${activePort}/api/projects/${projectId}/files/${encodeURIComponent(relPath)}`, {
      method: 'DELETE',
    })
      .then(r => r.json())
      .then(data => {
        if (data.status === 'deleted') {
          addLog(`[Files]: Deleted ${name}.`);
          fetchFiles(currentPath);
        }
      })
      .catch(() => addLog(`[Files]: Failed to delete ${name}.`));
  };

  const handleMkdir = () => {
    if (!newDirName.trim()) return;
    const name = newDirName.trim();
    fetch(`http://localhost:${activePort}/api/projects/${projectId}/files/mkdir?name=${encodeURIComponent(name)}`, {
      method: 'POST',
    })
      .then(r => r.json())
      .then(data => {
        if (data.status === 'created') {
          addLog(`[Files]: Created folder "${name}".`);
          setNewDirName('');
          setShowNewDir(false);
          fetchFiles(currentPath);
        }
      })
      .catch(() => addLog(`[Files]: Failed to create folder.`));
  };

  const handleDownload = (name) => {
    const relPath = currentPath ? `${currentPath}/${name}` : name;
    const url = `http://localhost:${activePort}/api/projects/${projectId}/files/download/${encodeURIComponent(relPath)}`;
    window.open(url, '_blank');
  };

  const onDrop = (e) => {
    e.preventDefault();
    setDragging(false);
    const files = e.dataTransfer.files;
    const archives = [];
    const regular = [];
    for (const f of files) {
      const ext = f.name.split('.').pop().toLowerCase();
      if (['zip', 'tar', 'gz', 'bz2', 'tgz', 'tbz2'].includes(ext)) {
        archives.push(f);
      } else {
        regular.push(f);
      }
    }
    if (regular.length > 0) handleUpload(regular);
    if (archives.length > 0) {
      archives.forEach(a => handleArchiveUpload(a));
    }
  };

  return (
    <div className="fixed inset-0 backdrop-blur-sm flex items-center justify-center p-4 z-50" style={{ backgroundColor: 'color-mix(in srgb, var(--bg-primary) 80%, transparent)' }}>
      <div className="rounded-xl w-full max-w-3xl overflow-hidden shadow-2xl flex flex-col" style={{ backgroundColor: 'var(--bg-card)', border: '1px solid var(--border)', maxHeight: '85vh' }}>
        <div className="p-4 flex justify-between items-center" style={{ backgroundColor: 'var(--bg-secondary)', borderBottom: '1px solid var(--border)' }}>
          <div>
            <h3 className="text-xs font-bold uppercase tracking-widest" style={{ color: 'var(--accent)' }}>📂 Project Files: {projectTitle || projectId}</h3>
            <p className="text-[10px] mt-0.5" style={{ color: 'var(--text-muted)' }}>/{currentPath || 'root'}</p>
          </div>
          <button onClick={onClose} className="font-mono text-sm" style={{ color: 'var(--text-muted)' }}>✕</button>
        </div>

        {/* Toolbar */}
        <div className="flex items-center space-x-2 p-3" style={{ backgroundColor: 'var(--bg-card)', borderBottom: '1px solid var(--border)' }}>
          <label className="px-3 py-1.5 rounded text-[10px] font-medium cursor-pointer transition-colors" style={{ backgroundColor: 'var(--accent)', color: '#fff' }}>
            📤 Upload Files
            <input type="file" multiple onChange={e => handleUpload(e.target.files)} className="hidden" />
          </label>
          <label className="px-3 py-1.5 rounded text-[10px] font-medium cursor-pointer transition-colors" style={{ backgroundColor: 'var(--success)', color: '#fff' }}>
            📦 Upload Archive
            <input type="file" accept=".zip,.tar,.tar.gz,.tgz,.tar.bz2,.tbz2" onChange={e => { if (e.target.files[0]) handleArchiveUpload(e.target.files[0]); }} className="hidden" />
          </label>
          <button onClick={() => setShowNewDir(!showNewDir)} className="px-3 py-1.5 rounded text-[10px] font-medium transition-colors" style={{ backgroundColor: 'var(--bg-secondary)', border: '1px solid var(--border)', color: 'var(--text-secondary)' }}>
            + New Folder
          </button>
          {parentPath && (
            <button onClick={() => fetchFiles(parentPath)} className="px-3 py-1.5 rounded text-[10px] font-medium transition-colors ml-auto" style={{ backgroundColor: 'var(--bg-secondary)', border: '1px solid var(--border)', color: 'var(--text-secondary)' }}>
              ← Back
            </button>
          )}
          {uploading && <span className="text-[10px] animate-pulse ml-auto" style={{ color: 'var(--accent)' }}>⏳ Uploading...</span>}
        </div>

        {/* New Folder */}
        {showNewDir && (
          <div className="flex items-center space-x-2 px-3 py-2" style={{ backgroundColor: 'var(--bg-primary)' }}>
            <input type="text" value={newDirName} onChange={e => setNewDirName(e.target.value)} placeholder="folder name" autoFocus className="flex-1 px-2 py-1 rounded text-xs focus:outline-none" style={{ backgroundColor: 'var(--bg-secondary)', border: '1px solid var(--border)', color: 'var(--text-primary)' }}
              onKeyDown={e => e.key === 'Enter' && handleMkdir()} />
            <button onClick={handleMkdir} className="px-2 py-1 rounded text-[10px] font-medium" style={{ backgroundColor: 'var(--accent)', color: '#fff' }}>Create</button>
            <button onClick={() => setShowNewDir(false)} className="px-2 py-1 rounded text-[10px]" style={{ color: 'var(--text-muted)' }}>Cancel</button>
          </div>
        )}

        {/* Drop zone + File list */}
        <div
          className="flex-1 overflow-y-auto p-3"
          style={{ backgroundColor: dragging ? 'var(--accent-bg)' : 'var(--bg-primary)', minHeight: 300 }}
          onDragOver={e => { e.preventDefault(); setDragging(true); }}
          onDragLeave={() => setDragging(false)}
          onDrop={onDrop}
        >
          {dragging && (
            <div className="absolute inset-0 flex items-center justify-center text-xs font-bold z-10" style={{ backgroundColor: 'color-mix(in srgb, var(--accent) 15%, transparent)', color: 'var(--accent)' }}>
              Drop files or archives anywhere
            </div>
          )}
          {loading ? (
            <div className="flex items-center justify-center h-32 text-[10px]" style={{ color: 'var(--text-muted)' }}>Loading...</div>
          ) : entries.length === 0 ? (
            <div className="flex flex-col items-center justify-center h-32 text-center">
              <p className="text-xs" style={{ color: 'var(--text-muted)' }}>No files yet</p>
              <p className="text-[10px] mt-1" style={{ color: 'var(--text-muted)' }}>Drag & drop files or use the Upload button</p>
            </div>
          ) : (
            <div className="grid grid-cols-1 gap-1">
              {entries.map((e, i) => {
                const color = e.is_dir ? DIR_COLOR : FILE_COLORS[fileType(e.name, false)];
                return (
                  <div key={i}
                    className="flex items-center px-3 py-2 rounded-lg transition-colors group"
                    style={{ backgroundColor: 'var(--bg-secondary)', border: '1px solid var(--border)' }}
                  >
                    <button
                      onClick={() => e.is_dir ? fetchFiles(currentPath ? `${currentPath}/${e.name}` : e.name) : handleDownload(e.name)}
                      className="flex items-center flex-1 min-w-0 text-left"
                      title={e.is_dir ? 'Open folder' : 'Download file'}
                    >
                      <span className="text-sm mr-2">{fileIcon(e.name, e.is_dir)}</span>
                      <span className="text-xs truncate" style={{ color: 'var(--text-primary)' }}>{e.name}</span>
                    </button>
                    <span className="text-[10px] mx-3 w-16 text-right" style={{ color: 'var(--text-muted)' }}>{formatSize(e.size)}</span>
                    <span className="text-[10px] w-20" style={{ color: 'var(--text-muted)' }}>{formatDate(e.modified)}</span>
                    <button onClick={() => handleDelete(e.name, e.is_dir)}
                      className="opacity-0 group-hover:opacity-100 text-[10px] px-2 transition-opacity"
                      style={{ color: 'var(--danger)' }}
                      title="Delete">✕</button>
                  </div>
                );
              })}
            </div>
          )}
        </div>

        <div className="p-3 flex justify-between items-center" style={{ backgroundColor: 'var(--bg-secondary)', borderTop: '1px solid var(--border)' }}>
          <span className="text-[10px]" style={{ color: 'var(--text-muted)' }}>{entries.length} item(s) · Max 50 MB</span>
          <button onClick={onClose} className="px-4 py-1.5 rounded text-xs font-medium" style={{ backgroundColor: 'var(--accent)', color: '#fff' }}>Close</button>
        </div>
      </div>
    </div>
  );
}
