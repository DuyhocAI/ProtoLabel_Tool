import React, {useEffect, useState} from "react";
import "./auth.css";

const api = async (path, method="GET", body) => {
  const response = await fetch(path, {
    method,
    credentials: "same-origin",
    headers: body ? {"Content-Type": "application/json"} : undefined,
    body: body ? JSON.stringify(body) : undefined,
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw Error(data.detail || `HTTP ${response.status}`);
  return data;
};

export function AuthScreen({onAuth}) {
  const [mode,setMode]=useState("login"), [username,setUsername]=useState(""), [password,setPassword]=useState(""), [displayName,setDisplayName]=useState(""), [error,setError]=useState(""), [busy,setBusy]=useState(false);
  const submit=async(e)=>{e.preventDefault();setBusy(true);setError("");try{const path=mode==="login"?"/api/auth/login":"/api/auth/register";const body=mode==="login"?{username,password}:{username,password,display_name:displayName};const result=await api(path,"POST",body);onAuth(result.user)}catch(err){setError(err.message)}finally{setBusy(false)}};
  return <main className="auth-page"><section className="auth-card"><img src="/protolabel-mark.svg" alt="ProtoLabel"/><div className="eyebrow">PROTOLABEL WORKSPACE</div><h1>{mode==="login"?"Đăng nhập":"Tạo tài khoản"}</h1><p>{mode==="login"?"Đăng nhập để tiếp tục labeling và lưu hiệu suất cá nhân.":"Tạo tài khoản annotator mới trong workspace."}</p><div className="auth-tabs"><button className={mode==="login"?"active":""} onClick={()=>{setMode("login");setError("")}}>Login</button><button className={mode==="register"?"active":""} onClick={()=>{setMode("register");setError("")}}>Register</button></div><form onSubmit={submit}>{mode==="register"&&<label>Tên hiển thị<input value={displayName} onChange={e=>setDisplayName(e.target.value)} maxLength={80} required/></label>}<label>Username<input value={username} onChange={e=>setUsername(e.target.value)} minLength={3} maxLength={32} autoComplete="username" required/></label><label>Password<input type="password" value={password} onChange={e=>setPassword(e.target.value)} minLength={mode==="register"?10:1} autoComplete={mode==="login"?"current-password":"new-password"} required/></label>{error&&<div className="auth-error">{error}</div>}<button className="primary" disabled={busy}>{busy?"Đang xử lý…":mode==="login"?"Đăng nhập":"Đăng ký"}</button></form>{mode==="login"&&<small>Tài khoản khởi tạo: admin / admin — bắt buộc đổi mật khẩu sau lần đăng nhập đầu.</small>}</section></main>;
}

export function ChangePassword({user,onChanged,onLogout}) {
  const [current,setCurrent]=useState(""), [next,setNext]=useState(""), [confirm,setConfirm]=useState(""), [error,setError]=useState("");
  const submit=async(e)=>{e.preventDefault();if(next!==confirm){setError("Mật khẩu xác nhận không khớp");return}try{const result=await api("/api/auth/change-password","PUT",{current_password:current,new_password:next});onChanged(result.user)}catch(err){setError(err.message)}};
  return <main className="auth-page"><section className="auth-card"><h1>Đổi mật khẩu</h1><p>Xin chào <b>{user.username}</b>. Tài khoản này cần đổi mật khẩu trước khi vào workspace.</p><form onSubmit={submit}><label>Mật khẩu hiện tại<input type="password" value={current} onChange={e=>setCurrent(e.target.value)} required/></label><label>Mật khẩu mới<input type="password" minLength={10} value={next} onChange={e=>setNext(e.target.value)} required/></label><label>Nhập lại mật khẩu mới<input type="password" minLength={10} value={confirm} onChange={e=>setConfirm(e.target.value)} required/></label>{error&&<div className="auth-error">{error}</div>}<button className="primary">Cập nhật mật khẩu</button><button type="button" className="ghost" onClick={onLogout}>Đăng xuất</button></form></section></main>;
}

const duration=(seconds)=>seconds<60?`${Math.round(seconds)}s`:seconds<3600?`${Math.round(seconds/60)}m`:`${(seconds/3600).toFixed(1)}h`;
export function AdminDashboard({currentUser,onClose,onLogout}) {
  const [users,setUsers]=useState([]), [registration,setRegistration]=useState(true), [error,setError]=useState("");
  const [projects,setProjects]=useState([]), [projectId,setProjectId]=useState(""), [period,setPeriod]=useState("30");
  const load=()=>{const query=new URLSearchParams();if(projectId)query.set("project_id",projectId);if(period!=="all")query.set("date_from",String(Date.now()/1000-Number(period)*86400));return Promise.all([api(`/api/admin/users?${query}`),api("/api/admin/settings"),api("/api/projects")]).then(([a,b,c])=>{setUsers(a.users);setRegistration(b.registration_enabled);setProjects(c.projects)}).catch(e=>setError(e.message))};
  useEffect(()=>{load()},[projectId,period]);
  const update=async(user,patch)=>{try{await api(`/api/admin/users/${user.id}`,"PUT",patch);await load()}catch(e){setError(e.message)}};
  const changeRole=(user,role)=>{if(role===user.role)return;if(confirm(`Đổi role của ${user.username} từ ${user.role} thành ${role}?`))update(user,{role})};
  const reset=(user)=>{const password=prompt(`Mật khẩu tạm mới cho ${user.username} (ít nhất 10 ký tự):`);if(password)update(user,{new_password:password})};
  const toggleRegistration=async()=>{try{const result=await api("/api/admin/settings","PUT",{registration_enabled:!registration});setRegistration(result.registration_enabled)}catch(e){setError(e.message)}};
  return <div className="admin-page"><header className="admin-head"><div><div className="eyebrow">ADMINISTRATION</div><h1>User performance</h1><p>Mỗi ảnh chỉ tính một lần cho mỗi người có thay đổi annotation thực tế.</p></div><div><button className="ghost" onClick={onClose}>← Workspace</button><button className="ghost" onClick={onLogout}>Logout</button></div></header><section className="admin-summary"><div><b>{users.length}</b><span>Users</span></div><div><b>{users.reduce((n,u)=>n+Number(u.images_saved||0),0).toLocaleString()}</b><span>Distinct images contributed</span></div><div><b>{users.reduce((n,u)=>n+Number(u.boxes_saved||0),0).toLocaleString()}</b><span>Latest boxes</span></div><div className="admin-filters"><label>Project <select value={projectId} onChange={e=>setProjectId(e.target.value)}><option value="">All projects</option>{projects.map(p=><option key={p.id} value={p.id}>{p.name}</option>)}</select></label><label>Period <select value={period} onChange={e=>setPeriod(e.target.value)}><option value="7">Last 7 days</option><option value="30">Last 30 days</option><option value="all">All time</option></select></label><label><input type="checkbox" checked={registration} onChange={toggleRegistration}/> Cho phép đăng ký mới</label></div></section>{error&&<div className="auth-error">{error}</div>}<section className="admin-table-wrap"><table className="admin-table"><thead><tr><th>User</th><th>Role</th><th>Images</th><th>Boxes</th><th>Prelabel</th><th>Active time</th><th>Last active</th><th>Account</th><th>Actions</th></tr></thead><tbody>{users.map(user=><tr key={user.id}><td><b>{user.display_name}</b><small>@{user.username}</small></td><td><select value={user.role} disabled={user.id===currentUser.id} onChange={e=>changeRole(user,e.target.value)}><option value="annotator">annotator</option><option value="admin">admin</option></select></td><td>{Number(user.images_saved||0).toLocaleString()}</td><td>{Number(user.boxes_saved||0).toLocaleString()}</td><td>{Number(user.prelabel_runs||0).toLocaleString()} / {Number(user.prelabel_images||0).toLocaleString()} ảnh</td><td>{duration(Number(user.active_seconds||0))}</td><td>{user.last_active?new Date(user.last_active*1000).toLocaleString():"—"}</td><td><span className={user.active?"user-active":"user-disabled"}>{user.active?"Active":"Disabled"}</span>{user.must_change_password&&<small>Must change password</small>}</td><td><button onClick={()=>reset(user)}>Reset password</button><button disabled={user.id===currentUser.id} onClick={()=>update(user,{active:!user.active})}>{user.active?"Disable":"Enable"}</button></td></tr>)}</tbody></table></section></div>;
}

export async function logout() { return api("/api/auth/logout","DELETE",{}); }
