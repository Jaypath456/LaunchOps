// LaunchOps live quiz worker. Django authorizes rooms and serves questions;
// Redis coordinates room state across replicas; MongoDB durably stores answers.
package main

import (
	"context"
	"encoding/json"
	"fmt"
	"log"
	"net/http"
	"os"
	"strconv"
	"strings"
	"sync"
	"time"

	"github.com/gorilla/websocket"
	"github.com/redis/go-redis/v9"
	"go.mongodb.org/mongo-driver/bson"
	"go.mongodb.org/mongo-driver/mongo"
	"go.mongodb.org/mongo-driver/mongo/options"
)

type question struct {
	Public struct {
		ID int64 `json:"id"`
		Type string `json:"question_type"`
		Points int64 `json:"points"`
		TimeLimit int64 `json:"time_limit_seconds"`
		Body struct { Options []struct { ID string `json:"id"` } `json:"options"` } `json:"body"`
	} `json:"public"`
	RawPublic json.RawMessage `json:"-"`
	AnswerKey json.RawMessage `json:"answer_key"`
}
type bootstrap struct {
	Room string `json:"room"`
	Status string `json:"status"`
	Host bool `json:"host"`
	UserID int64 `json:"user_id"`
	Username string `json:"username"`
	Questions []question `json:"questions"`
}
type wireQuestion struct {
	Public json.RawMessage `json:"public"`
	AnswerKey json.RawMessage `json:"answer_key"`
}
func (q *question) UnmarshalJSON(data []byte) error {
	var w wireQuestion
	if err := json.Unmarshal(data,&w);err!=nil{return err}
	q.RawPublic=w.Public
	q.AnswerKey=w.AnswerKey
	return json.Unmarshal(w.Public,&q.Public)
}
type answer struct {
	Room string `bson:"room"`
	QuestionID int64 `bson:"question_id"`
	UserID int64 `bson:"user_id"`
	Username string `bson:"username"`
	Answer json.RawMessage `bson:"answer"`
	Correct bool `bson:"correct"`
	Points int64 `bson:"points"`
	Buckets []string `bson:"buckets"`
	CreatedAt time.Time `bson:"created_at"`
}
type server struct {
	redis *redis.Client
	answers *mongo.Collection
	backend string
	secret string
	client *http.Client
	upgrader websocket.Upgrader
	hubMu sync.Mutex
	hubs map[string]*roomHub
}
type roomHub struct {
	pub *redis.PubSub
	clients map[chan map[string]any]bootstrap
}

func env(key, fallback string) string { if v := os.Getenv(key); v != "" { return v }; return fallback }
func key(room, suffix string) string { return "go:live:" + room + ":" + suffix }
func send(ch chan<- map[string]any, value map[string]any) {
	select { case ch <- value: case <-time.After(time.Second): } // Per-client backpressure.
}

func main() {
	ctx := context.Background()
	secret := os.Getenv("LIVE_SERVICE_SECRET")
	if len(secret) < 32 { log.Fatal("LIVE_SERVICE_SECRET must have at least 32 characters") }
	r := redis.NewClient(&redis.Options{Addr: env("REDIS_ADDR", "redis:6379"), PoolSize: 128})
	if err := r.Ping(ctx).Err(); err != nil { log.Fatal(err) }
	m, err := mongo.Connect(ctx, options.Client().ApplyURI(env("MONGO_URI", "mongodb://mongo:27017")))
	if err != nil { log.Fatal(err) }
	if err = m.Ping(ctx, nil); err != nil { log.Fatal(err) }
	a := m.Database(env("MONGO_DB", "launchops_live")).Collection("live_answers")
	_, err = a.Indexes().CreateOne(ctx, mongo.IndexModel{Keys: bson.D{{Key:"room",Value:1},{Key:"question_id",Value:1},{Key:"user_id",Value:1}}, Options: options.Index().SetUnique(true)})
	if err != nil { log.Fatal(err) }
	s := &server{redis:r, answers:a, hubs:make(map[string]*roomHub), backend:env("BACKEND_URL", "http://backend:8000"), secret:secret,
		client:&http.Client{Timeout:15*time.Second, Transport:&http.Transport{MaxIdleConns:256, MaxIdleConnsPerHost:128}},
		upgrader:websocket.Upgrader{CheckOrigin:func(r *http.Request) bool {
			origin := r.Header.Get("Origin")
			for _, allowed := range strings.Split(env("ALLOWED_ORIGINS", "http://localhost:13000"), ",") {
				if origin == strings.TrimSpace(allowed) { return true }
			}
			return origin == "" // CLI load clients send no Origin; JWT still required.
		}},
	}
	http.HandleFunc("/health", func(w http.ResponseWriter,r *http.Request){w.WriteHeader(http.StatusOK); _,_ = w.Write([]byte("ok"))})
	http.HandleFunc("/ws/live/", s.connect)
	log.Fatal(http.ListenAndServe(":8080", nil))
}

func (s *server) backendRequest(ctx context.Context, room, token, action string) (*http.Response,error) {
	method := http.MethodGet
	if action == "finish" { method = http.MethodPost }
	req, err := http.NewRequestWithContext(ctx, method, s.backend+"/api/_internal/live/"+room+"/"+action+"/", nil)
	if err != nil { return nil,err }
	req.Header.Set("Authorization", "Bearer "+token)
	req.Header.Set("X-Live-Service", s.secret)
	return s.client.Do(req)
}
// One Redis subscription per active room per replica, regardless of how many
// students are connected. Every connection retains a bounded local queue.
func (s *server) subscribe(room string, b bootstrap) (<-chan map[string]any,func(),error) {
	s.hubMu.Lock()
	hub:=s.hubs[room]
	if hub==nil {
		pub:=s.redis.Subscribe(context.Background(),key(room,"events"))
		if _,err:=pub.Receive(context.Background());err!=nil {s.hubMu.Unlock();pub.Close();return nil,nil,err}
		hub=&roomHub{pub:pub,clients:make(map[chan map[string]any]bootstrap)}
		s.hubs[room]=hub
		go func() {
			for evt:=range pub.Channel() {
				var message map[string]any
				if json.Unmarshal([]byte(evt.Payload),&message)!=nil {continue}
				s.hubMu.Lock()
				for ch,user:=range hub.clients {
					if !user.Host && (message["type"]=="chart.update" || message["type"]=="leaderboard.update") {continue}
					payload:=message
					if user.Host && message["type"]=="question.revealed" {
						payload=make(map[string]any,len(message)+1)
						for k,v:=range message {payload[k]=v}
						idx:=int(message["question_index"].(float64))
						if idx>=0 && idx<len(user.Questions) {addHostKey(payload,user.Questions[idx])}
					}
					select {case ch<-payload:default: /* reconnecting client gets snapshot */}
				}
				s.hubMu.Unlock()
			}
		}()
	}
	ch:=make(chan map[string]any,1024)
	hub.clients[ch]=b
	s.hubMu.Unlock()
	cleanup:=func(){s.hubMu.Lock();defer s.hubMu.Unlock();delete(hub.clients,ch);if len(hub.clients)==0 {delete(s.hubs,room);hub.pub.Close()}}
	return ch,cleanup,nil
}
func (s *server) connect(w http.ResponseWriter,r *http.Request) {
	parts := strings.Split(strings.Trim(r.URL.Path,"/"),"/")
	if len(parts)!=3 || parts[0]!="ws" || parts[1]!="live" || len(parts[2])!=6 { http.Error(w,"Invalid room",400); return }
	room := parts[2]
	// Browser sends its existing JWT as a WebSocket subprotocol. Never put
	// bearer tokens into a URL or a Redis message.
	protocols := websocket.Subprotocols(r)
	if len(protocols)!=1 || len(protocols[0])>4096 { http.Error(w,"Authentication required",401); return }
	token := protocols[0]
	res,err := s.backendRequest(r.Context(),room,token,"bootstrap")
	if err!=nil { http.Error(w,"Authorization service unavailable",503); return }
	defer res.Body.Close()
	if res.StatusCode!=200 { http.Error(w,"Room access denied",res.StatusCode); return }
	var b bootstrap
	if err=json.NewDecoder(http.MaxBytesReader(w,res.Body,1<<20)).Decode(&b); err!=nil { http.Error(w,"Invalid room state",503); return }
	if b.Room!=room || len(b.Questions)==0 { http.Error(w,"Room has no questions",409); return }
	// State is keyed per room in Redis, not per replica. We do not overwrite
	// a room advanced by another worker on a new connection.
	stateKey:=key(room,"state")
	if err=s.redis.HSetNX(r.Context(),stateKey,"status",b.Status).Err(); err!=nil {http.Error(w,"Room unavailable",503);return}
	s.redis.HSetNX(r.Context(),stateKey,"index",-1)
	if b.Status=="ended" { s.redis.HSet(r.Context(),stateKey,"status","ended") }
	if b.Status=="active" { current,_:=s.redis.HGet(r.Context(),stateKey,"status").Result();if current!="ended" {s.redis.HSet(r.Context(),stateKey,"status","active")} }
	if r.Header.Get("Origin")!="" && !s.upgrader.CheckOrigin(r) {http.Error(w,"Origin denied",403);return}
	stream,unsubscribe,err:=s.subscribe(room,b)
	if err!=nil {http.Error(w,"Room unavailable",503);return}
	defer unsubscribe()
	conn,err:=s.upgrader.Upgrade(w,r,http.Header{"Sec-WebSocket-Protocol":[]string{token}})
	if err!=nil {return}
	defer conn.Close()
	ctx,cancel:=context.WithCancel(context.Background());defer cancel()
	out:=make(chan map[string]any,1024)
	go func(){for {select {case <-ctx.Done():return;case message:=<-stream:send(out,message)}}}()
	go func(){defer cancel();defer conn.Close();for {select {case <-ctx.Done():return;case msg:=<-out:
		conn.SetWriteDeadline(time.Now().Add(5*time.Second))
		if conn.WriteJSON(msg)!=nil {return}
	}}}()
	s.snapshot(ctx,room,b,out)
	conn.SetReadLimit(8192)
	for {
		_,payload,err:=conn.ReadMessage();if err!=nil {return}
		var req struct {Type string `json:"type"`; QuestionID int64 `json:"question_id"`; Answer json.RawMessage `json:"answer"`}
		if json.Unmarshal(payload,&req)!=nil {send(out,map[string]any{"error":"Invalid message format."});continue}
		switch req.Type {
		case "question.advance": s.advance(ctx,room,b,out)
		case "answer.submit": s.submit(ctx,room,b,req.QuestionID,req.Answer,out)
		case "session.end":
			if !b.Host {send(out,map[string]any{"error":"Only the host can end the session."});continue}
			if reply,e:=s.backendRequest(ctx,room,token,"finish");e!=nil {send(out,map[string]any{"error":"Could not end session."});continue} else {reply.Body.Close();if reply.StatusCode!=200 {send(out,map[string]any{"error":"Could not end session."});continue}}
			s.redis.HSet(ctx,stateKey,"status","ended")
			s.redis.Expire(ctx,stateKey,7*24*time.Hour)
			s.redis.Expire(ctx,key(room,"scores"),7*24*time.Hour)
			s.publish(ctx,room,map[string]any{"type":"session.ended"})
		default:send(out,map[string]any{"error":"Unknown event type."})
		}
	}
}

func addHostKey(message map[string]any,q question) {
	if q.Public.Type=="short_answer" {var a string;_ = json.Unmarshal(q.AnswerKey,&a);message["correct_answer"]=a} else {var a []string;_ = json.Unmarshal(q.AnswerKey,&a);message["correct_option_ids"]=a}
}
func (s *server) publish(ctx context.Context,room string,payload map[string]any) {
	bytes,_:=json.Marshal(payload)
	if err:=s.redis.Publish(ctx,key(room,"events"),bytes).Err();err!=nil {log.Printf("publish: %v",err)}
}
func (s *server) standings(ctx context.Context,room string) []map[string]any {
	values,err:=s.redis.ZRevRangeWithScores(ctx,key(room,"scores"),0,9).Result();if err!=nil {return nil}
	result:=make([]map[string]any,0,len(values))
	for i,v:=range values {fields:=strings.SplitN(v.Member.(string),"|",2);if len(fields)!=2 {continue};uid,_:=strconv.ParseInt(fields[0],10,64);result=append(result,map[string]any{"user_id":uid,"username":fields[1],"score":int64(v.Score),"rank":i+1})}
	return result
}
func (s *server) snapshot(ctx context.Context,room string,b bootstrap,out chan<- map[string]any) {
	state,_:=s.redis.HGetAll(ctx,key(room,"state")).Result();idx,_:=strconv.Atoi(state["index"])
	message:=map[string]any{"type":"session.state","status":state["status"],"question_index":idx,"revealed_at":state["revealed_at"]}
	if b.Host && state["status"]=="active" {message["leaderboard"]=s.standings(ctx,room)}
	if state["status"]=="active" && idx>=0 && idx<len(b.Questions) {
		var pub any;_ = json.Unmarshal(b.Questions[idx].RawPublic,&pub);message["question"]=pub
		if b.Host {
			chart,_:=s.redis.HGetAll(ctx,key(room,fmt.Sprintf("chart:%d",b.Questions[idx].Public.ID))).Result()
			message["chart"]=chart
			addHostKey(message,b.Questions[idx])
		}
	}
	send(out,message)
}
var advanceScript=redis.NewScript(`
if redis.call('HGET',KEYS[1],'status') ~= 'active' then return -1 end
local n=tonumber(redis.call('HGET',KEYS[1],'index') or '-1')+1
if n >= tonumber(ARGV[1]) then return -1 end
redis.call('HSET',KEYS[1],'index',n,'revealed_at',ARGV[2],'revealed_ms',ARGV[3]);return n`)
func (s *server) advance(ctx context.Context,room string,b bootstrap,out chan<- map[string]any) {
	if !b.Host {send(out,map[string]any{"error":"Only the host can advance questions."});return}
	now:=time.Now().UTC()
	idx,err:=advanceScript.Run(ctx,s.redis,[]string{key(room,"state")},len(b.Questions),now.Format(time.RFC3339Nano),now.UnixMilli()).Int()
	if err!=nil || idx<0 {send(out,map[string]any{"error":"Session is not active, or there are no more questions."});return}
	var pub any;_ = json.Unmarshal(b.Questions[idx].RawPublic,&pub)
	s.publish(ctx,room,map[string]any{"type":"question.revealed","question":pub,"question_index":idx,"revealed_at":now.Format(time.RFC3339Nano)})
}
var claimScript=redis.NewScript(`
if redis.call('HGET',KEYS[1],'status') ~= 'active' or tonumber(redis.call('HGET',KEYS[1],'index') or '-1') ~= tonumber(ARGV[1]) then return -1 end
if tonumber(ARGV[2])-tonumber(redis.call('HGET',KEYS[1],'revealed_ms') or '0') > tonumber(ARGV[3])*1000 then return -1 end
if redis.call('SET',KEYS[2],ARGV[4],'NX','EX',86400) then return 1 end
return 0`)
func isCorrect(q question,raw json.RawMessage)(bool,[]string) {
	if q.Public.Type=="short_answer" {
		var given,want string
		if json.Unmarshal(raw,&given)!=nil {return false,nil}
		if len(given)>240 {return false,nil}
		_ = json.Unmarshal(q.AnswerKey,&want)
		ok:=strings.EqualFold(strings.TrimSpace(given),strings.TrimSpace(want))
		if ok{return true,[]string{"correct"}};return false,[]string{"incorrect"}
	}
	var given,want []string
	if json.Unmarshal(raw,&given)!=nil||json.Unmarshal(q.AnswerKey,&want)!=nil{return false,nil}
	if q.Public.Type=="single_choice" && len(given)!=1{return false,nil}
	allowed:=map[string]bool{};for _,o:=range q.Public.Body.Options{allowed[o.ID]=true}
	seen:=map[string]bool{};for _,x:=range given{if !allowed[x] || seen[x] {return false,nil};seen[x]=true}
	if len(given)>len(allowed) {return false,nil}
	ok:=len(seen)==len(want);for _,x:=range want {if !seen[x]{ok=false}}
	return ok,given
}
func (s *server) submit(ctx context.Context,room string,b bootstrap,qid int64,raw json.RawMessage,out chan<- map[string]any) {
	if b.Host {send(out,map[string]any{"error":"Host cannot submit answers."});return}
	state,_:=s.redis.HGetAll(ctx,key(room,"state")).Result();idx,_:=strconv.Atoi(state["index"])
	if idx<0||idx>=len(b.Questions)||b.Questions[idx].Public.ID!=qid {send(out,map[string]any{"error":"This question is not currently open."});return}
	q:=b.Questions[idx];correct,buckets:=isCorrect(q,raw)
	if buckets==nil {send(out,map[string]any{"error":"Invalid answer for this question."});return}
	claim:=key(room,fmt.Sprintf("claim:%d:%d",qid,b.UserID));nonce:=fmt.Sprintf("%d",time.Now().UnixNano())
	claimed,err:=claimScript.Run(ctx,s.redis,[]string{key(room,"state"),claim},idx,time.Now().UnixMilli(),q.Public.TimeLimit,nonce).Int()
	if err!=nil {send(out,map[string]any{"error":"Answer service unavailable."});return}
	if claimed<0 {send(out,map[string]any{"error":"This question is not currently open."});return}
	filter:=bson.M{"room":room,"question_id":qid,"user_id":b.UserID}
	if claimed==0 {var old answer;if s.answers.FindOne(ctx,filter).Decode(&old)==nil {send(out,map[string]any{"type":"answer.accepted","question_id":qid,"is_correct":old.Correct})} else {send(out,map[string]any{"error":"Answer is still being saved; retry."})};return}
	points:=int64(0);if correct{points=q.Public.Points}
	entry:=answer{Room:room,QuestionID:qid,UserID:b.UserID,Username:b.Username,Answer:raw,Correct:correct,Points:points,Buckets:buckets,CreatedAt:time.Now().UTC()}
	_,err=s.answers.InsertOne(ctx,entry)
	if err!=nil {
		// A second replica may have written the same key. Mongo's unique
		// index is the durable idempotency guard.
		if mongo.IsDuplicateKeyError(err) {var old answer;if s.answers.FindOne(ctx,filter).Decode(&old)==nil {send(out,map[string]any{"type":"answer.accepted","question_id":qid,"is_correct":old.Correct});return}}
		s.redis.Eval(ctx,`if redis.call('GET',KEYS[1])==ARGV[1] then return redis.call('DEL',KEYS[1]) end return 0`,[]string{claim},nonce)
		send(out,map[string]any{"error":"Answer could not be saved; retry."});return
	}
	// Private correctness goes only to this WebSocket, never Redis Pub/Sub.
	send(out,map[string]any{"type":"answer.accepted","question_id":qid,"is_correct":correct})
	chart:=key(room,fmt.Sprintf("chart:%d",qid))
	pipe:=s.redis.TxPipeline()
	for _,bucket:=range buckets {pipe.HIncrBy(ctx,chart,bucket,1)}
	pipe.ZIncrBy(ctx,key(room,"scores"),float64(points),fmt.Sprintf("%d|%s",b.UserID,b.Username))
	if _,err=pipe.Exec(ctx);err!=nil {log.Printf("room %s score cache requires reconciliation: %v",room,err);return}
	counts,_:=s.redis.HGetAll(ctx,chart).Result()
	s.publish(ctx,room,map[string]any{"type":"chart.update","question_id":qid,"counts":counts})
	s.publish(ctx,room,map[string]any{"type":"leaderboard.update","rankings":s.standings(ctx,room)})
}
